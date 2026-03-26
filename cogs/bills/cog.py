"""
Bills Cog — track NJ Legislature bills and send @everyone alerts on updates.

Commands (prefix !):  [must be sent in CH_MBT_COMMAND_ID]
  !addbill <bill_number>           — start tracking a bill (e.g. !addbill S1234)
  !addbills                        — bulk-add bills from an attached .csv or .xlsx
  !removebill <bill_number>        — stop tracking a bill
  !listbills                       — show all tracked bills with current status
  !billhistory <bill_number>       — show the full legislative history for a bill
  !checkbills                      — force an immediate update poll
  !setpollhours <hours>            — change the polling interval at runtime
  !billreport <bill_number|all>    — upload Excel report to CH_MBT_REPORTS_ID
    e.g. !billreport S1234
         !billreport all
         !billreport S1234,A567    (comma-separated subset)

Environment variables:
  CH_MBT_COMMAND_ID    Discord channel ID where commands are accepted
  CH_MBT_REPORTS_ID    Discord channel ID where Excel reports are uploaded
  CH_MBT_ALERT_ID      Discord channel ID for @everyone bill update alerts
  LEGISCAN_API_KEY     LegiScan API key (free at legiscan.com)
  BILLS_POLL_HOURS     How often to poll for updates (default: 6)
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import os
import re
from pathlib import Path

import discord
import icalendar
import openpyxl
from discord.ext import commands, tasks

from .legiscan import LegiScanClient, LegiScanError
from .reporter import build_all_report, build_single_report
from .storage import BillsStorage

log = logging.getLogger("homebot.bills")

# Matches NJ-style bill numbers: S1234, A567, SCR1, ACR2, SR1, AR1, etc.
_BILL_RE = re.compile(r"\b[A-Z]{1,4}\d+\b")


def _extract_bill_numbers(text: str) -> list[str]:
    """Return deduplicated bill numbers found in a block of text."""
    return list(dict.fromkeys(_BILL_RE.findall(text.upper())))


def _parse_csv_attachment(data: bytes) -> list[str]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    combined = ",".join(cell for row in reader for cell in row)
    return _extract_bill_numbers(combined)


def _parse_excel_attachment(data: bytes) -> list[str]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    cells: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    cells.append(str(cell))
    wb.close()
    return _extract_bill_numbers(",".join(cells))


class BillsCog(commands.Cog):
    """Tracks NJ Legislature bills and sends alerts when they are updated."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Set commands channel id
        raw_cmd = os.environ.get("CH_MBT_COMMAND_ID")
        if not raw_cmd:
            raise RuntimeError("CH_MBT_COMMAND_ID is not set in the environment.")
        try:
            self.channel_id = int(raw_cmd)
        except ValueError:
            raise RuntimeError(f"CH_MBT_COMMAND_ID must be an integer, got: {raw_cmd!r}")

        # Set reports channel id
        raw_reports = os.environ.get("CH_MBT_REPORTS_ID")
        if not raw_reports:
            raise RuntimeError("CH_MBT_REPORTS_ID is not set in the environment.")
        try:
            self.reports_channel_id = int(raw_reports)
        except ValueError:
            raise RuntimeError(f"CH_MBT_REPORTS_ID must be an integer, got: {raw_reports!r}")

        # Set alerts channel id
        raw_alert = os.environ.get("CH_MBT_ALERT_ID")
        if not raw_alert:
            raise RuntimeError("CH_MBT_ALERT_ID is not set in the environment.")
        try:
            self.alert_channel_id = int(raw_alert)
        except ValueError:
            raise RuntimeError(f"CH_MBT_ALERT_ID must be an integer, got: {raw_alert!r}")

        api_key = os.environ.get("LEGISCAN_API_KEY")
        if not api_key:
            raise RuntimeError("LEGISCAN_API_KEY is not set in the environment.")
        self.client = LegiScanClient(api_key)

        # Storage instance provided by the shared StorageManager
        self.storage: BillsStorage = bot.storage_manager.get("bills")
        log.info("BillsCog using storage instance 0x%x", id(self.storage))

        poll_hours = int(os.environ.get("BILLS_POLL_HOURS", "6"))
        self._poll.change_interval(hours=poll_hours)
        self._poll.start()
        log.info("Bills cog started — polling every %d hour(s)", poll_hours)

    def cog_unload(self) -> None:
        self._poll.cancel()

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Only process commands sent in the designated commands channel."""
        if ctx.channel.id != self.channel_id:
            log.debug(
                "Bills command '%s' from %s ignored — wrong channel (%d)",
                ctx.command.name, ctx.author, ctx.channel.id,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Background poll
    # ------------------------------------------------------------------

    @tasks.loop(hours=6)
    async def _poll(self) -> None:
        """Fetch LegiScan master list, detect changed bills, send alerts."""
        bills = self.storage.list_bills()
        if not bills:
            log.debug("Poll skipped — no bills are being tracked")
            return

        log.info("Poll started — %d bill(s) tracked", len(bills))
        try:
            master = await self.client.get_master_list()
        except LegiScanError as exc:
            log.error("Poll failed — could not fetch master list: %s", exc)
            return

        checked = 0
        updated = 0
        alerted = 0

        for bill in bills:
            bill_number = bill.get("bill_number", "")
            state = bill.get("state", "NJ")
            key = self.storage.bill_key(bill_number, state)
            checked += 1

            entry = master.get(bill_number)
            if not entry:
                log.debug("Poll: %s not found in master list", bill_number)
                continue

            old_hash = bill.get("change_hash", "")
            new_hash = entry["change_hash"]
            if new_hash == old_hash:
                log.debug("Poll: %s unchanged (hash=%s)", bill_number, old_hash)
                continue

            log.info(
                "Poll: %s changed — change_hash %s → %s",
                bill_number, old_hash or "(none)", new_hash,
            )

            # Change detected — fetch full bill details
            try:
                updated_data = await self.client.get_bill(entry["bill_id"])
            except LegiScanError as exc:
                log.error("Poll: failed to fetch bill %s: %s", bill_number, exc)
                continue

            # Merge fetched data into stored bill (preserve added_by, added_at, etc.)
            merged = {**bill, **updated_data}
            merged["last_checked"] = datetime.datetime.now().isoformat()
            updated += 1

            new_action_date = updated_data.get("last_action_date", "")
            alerted_date = bill.get("last_alerted_action_date", "")

            if new_action_date and new_action_date > alerted_date:
                await self._send_alert(merged)
                merged["last_alerted_action_date"] = new_action_date
                alerted += 1

            self.storage.save_bill(key, merged)
            log.info(
                "Poll: updated %s — action: %s",
                bill_number, updated_data.get("last_action"),
            )

        log.info(
            "Poll complete — %d checked, %d updated, %d alerted",
            checked, updated, alerted,
        )

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_alert(self, bill: dict) -> None:
        channel = self.bot.get_channel(self.alert_channel_id)
        if not channel:
            log.warning("Bills alerts channel (CH_MBT_ALERT_ID=%d) not found", self.alert_channel_id)
            return

        await channel.send(
            f"@everyone\n"
            f"📋 **BILL UPDATE:** {bill['bill_number']} — {bill['title']}\n"
            f"**New Action:** {bill['last_action']}\n"
            f"**Date:** {bill['last_action_date']}\n"
            f"**Status:** {bill['status']}\n"
            f"🔗 {bill['url']}"
        )
        log.info("Bills alert: sent update for %s to alerts channel", bill["bill_number"])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="addbill")
    async def add_bill(self, ctx: commands.Context, bill_number: str) -> None:
        """Start tracking a bill. Usage: !addbill S1234"""
        bill_number = bill_number.upper()
        log.info("Command 'addbill' by %s — bill_number=%s", ctx.author, bill_number)
        key = self.storage.bill_key(bill_number)

        if self.storage.get_bill(key):
            await ctx.send(f"`{bill_number}` is already being tracked.")
            return

        async with ctx.typing():
            try:
                result = await self.client.search_bill(bill_number)
            except LegiScanError as exc:
                log.error("LegiScan search error for %s: %s", bill_number, exc)
                await ctx.send(f"Error searching for `{bill_number}`: {exc}")
                return

        if not result:
            await ctx.send(f"Bill `{bill_number}` not found on LegiScan.")
            return

        try:
            full = await self.client.get_bill(result["bill_id"])
        except LegiScanError as exc:
            log.error("LegiScan fetch error for %s: %s", bill_number, exc)
            await ctx.send(f"Found bill but could not fetch details: {exc}")
            return

        now = datetime.datetime.now().isoformat()
        record = {
            **full,
            "added_at": now,
            "added_by": str(ctx.author),
            "last_checked": now,
            "last_alerted_action_date": full.get("last_action_date", ""),
        }
        self.storage.save_bill(key, record)
        log.info("addbill: %s added bill %s", ctx.author, bill_number)

        embed = discord.Embed(
            title=f"✅ Now tracking {bill_number}",
            description=full.get("title", ""),
            color=discord.Color.green(),
        )
        embed.add_field(name="Status", value=full.get("status", "—"))
        embed.add_field(name="Last Action", value=full.get("last_action", "—"), inline=False)
        embed.add_field(name="URL", value=full.get("url", "—"), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="addbills")
    async def add_bills_bulk(self, ctx: commands.Context) -> None:
        """
        Bulk-add bills from a CSV or Excel attachment.

        Attach a .csv or .xlsx file containing bill numbers separated by
        commas, newlines, or in individual cells.  Usage: !addbills
        """
        log.info("Command 'addbills' by %s — attachment count=%d", ctx.author, len(ctx.message.attachments))
        if not ctx.message.attachments:
            await ctx.send("Please attach a `.csv` or `.xlsx` file.")
            return

        attachment = ctx.message.attachments[0]
        name = attachment.filename.lower()
        if not (name.endswith(".csv") or name.endswith(".xlsx") or name.endswith(".xls")):
            await ctx.send("Unsupported file type. Please attach a `.csv` or `.xlsx` file.")
            return

        async with ctx.typing():
            data = await attachment.read()
            try:
                if name.endswith(".csv"):
                    bill_numbers = _parse_csv_attachment(data)
                else:
                    bill_numbers = _parse_excel_attachment(data)
            except Exception as exc:
                log.error("Failed to parse attachment '%s': %s", attachment.filename, exc)
                await ctx.send(f"Failed to parse file: {exc}")
                return

        if not bill_numbers:
            await ctx.send("No bill numbers found in the attached file.")
            return

        log.info("addbills: found %d bill number(s) in attachment from %s", len(bill_numbers), ctx.author)
        await ctx.send(f"Found {len(bill_numbers)} bill number(s). Adding...")

        added, skipped, failed = [], [], []

        async with ctx.typing():
            for bill_number in bill_numbers:
                key = self.storage.bill_key(bill_number)
                if self.storage.get_bill(key):
                    skipped.append(bill_number)
                    continue
                try:
                    result = await self.client.search_bill(bill_number)
                except LegiScanError as exc:
                    log.error("LegiScan search error for %s: %s", bill_number, exc)
                    failed.append(bill_number)
                    continue
                if not result:
                    failed.append(bill_number)
                    continue
                try:
                    full = await self.client.get_bill(result["bill_id"])
                except LegiScanError as exc:
                    log.error("LegiScan fetch error for %s: %s", bill_number, exc)
                    failed.append(bill_number)
                    continue
                now = datetime.datetime.now().isoformat()
                record = {
                    **full,
                    "added_at": now,
                    "added_by": str(ctx.author),
                    "last_checked": now,
                    "last_alerted_action_date": full.get("last_action_date", ""),
                }
                self.storage.save_bill(key, record)
                added.append(bill_number)
                log.info("addbills: %s added %s", ctx.author, bill_number)

        log.info(
            "addbills complete — added=%d skipped=%d failed=%d",
            len(added), len(skipped), len(failed),
        )
        lines = []
        if added:
            lines.append(f"✅ Added ({len(added)}): {', '.join(added)}")
        if skipped:
            lines.append(f"⏭️ Already tracked ({len(skipped)}): {', '.join(skipped)}")
        if failed:
            lines.append(f"❌ Not found / error ({len(failed)}): {', '.join(failed)}")
        await ctx.send("\n".join(lines))

    @commands.command(name="removebill")
    async def remove_bill(self, ctx: commands.Context, bill_number: str) -> None:
        """Stop tracking a bill. Usage: !removebill S1234"""
        bill_number = bill_number.upper()
        log.info("Command 'removebill' by %s — bill_number=%s", ctx.author, bill_number)
        key = self.storage.bill_key(bill_number)

        if not self.storage.get_bill(key):
            await ctx.send(f"`{bill_number}` is not being tracked.")
            return

        self.storage.remove_bill(key)
        log.info("removebill: %s removed bill %s", ctx.author, bill_number)
        await ctx.send(f"❌ Stopped tracking `{bill_number}`.")

    @commands.command(name="listbills")
    async def list_bills(self, ctx: commands.Context) -> None:
        """Show all currently tracked bills and their status."""
        log.info("Command 'listbills' by %s", ctx.author)
        bills = self.storage.list_bills()
        if not bills:
            await ctx.send("No bills are being tracked. Use `!addbill <number>` to add one.")
            return

        embed = discord.Embed(
            title=f"📋 Tracked Bills ({len(bills)})",
            color=discord.Color.blue(),
        )
        for bill in bills:
            value = (
                f"**Status:** {bill.get('status', '—')}\n"
                f"**Last Action:** {bill.get('last_action', '—')}\n"
                f"**Date:** {bill.get('last_action_date', '—')}\n"
                f"🔗 {bill.get('url', '')}"
            )
            embed.add_field(
                name=f"{bill.get('bill_number', '?')} — {bill.get('title', '')[:60]}",
                value=value,
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="billhistory")
    async def bill_history(self, ctx: commands.Context, bill_number: str) -> None:
        """Show legislative history for a tracked bill. Usage: !billhistory S1234"""
        bill_number = bill_number.upper()
        log.info("Command 'billhistory' by %s — bill_number=%s", ctx.author, bill_number)
        key = self.storage.bill_key(bill_number)
        bill = self.storage.get_bill(key)
        if not bill:
            await ctx.send(f"`{bill_number}` is not being tracked.")
            return

        history = bill.get("history", [])
        if not history:
            await ctx.send(f"No history available for `{bill_number}`.")
            return

        # Show most recent first, capped at 10 to stay within Discord embed limits
        recent = list(reversed(history))[:10]
        embed = discord.Embed(
            title=f"📋 History: {bill_number}",
            description=bill.get("title", ""),
            color=discord.Color.blue(),
        )
        for entry in recent:
            chamber = entry.get("chamber", "")
            label = f"{entry.get('date', '—')}"
            if chamber:
                label += f" [{chamber}]"
            embed.add_field(name=label, value=entry.get("action", "—"), inline=False)

        if len(history) > 10:
            embed.set_footer(text=f"Showing 10 most recent of {len(history)} total action(s)")
        else:
            embed.set_footer(text=f"{len(history)} action(s) total")

        await ctx.send(embed=embed)

    @commands.command(name="checkbills")
    async def check_bills(self, ctx: commands.Context) -> None:
        """Force an immediate update poll for all tracked bills."""
        log.info("Command 'checkbills' by %s", ctx.author)
        await ctx.send("🔄 Checking for bill updates...")
        await self._poll()
        await ctx.send("✅ Done checking.")

    @commands.command(name="setpollhours")
    async def set_poll_hours(self, ctx: commands.Context, hours: int) -> None:
        """Change the bill polling interval. Usage: !setpollhours <hours>"""
        log.info("Command 'setpollhours' by %s — hours=%d", ctx.author, hours)
        if hours < 1:
            await ctx.send("Poll interval must be at least 1 hour.")
            return
        self._poll.change_interval(hours=hours)
        log.info("Bills poll interval changed to %d hour(s) by %s", hours, ctx.author)
        await ctx.send(f"✅ Poll interval updated to every {hours} hour(s).")

    @commands.command(name="refreshdb")
    async def refresh_db(self, ctx: commands.Context) -> None:
        """
        Restore bills and calendar from channel history + saved ICS exports.

        Only allowed when the bills database is empty (guard against accidental
        use on a live system).  Run this after a fresh deploy with an empty
        data volume.

        Bills:   scans this channel's message history for !addbill commands
                 and re-adds each bill via LegiScan.
        Calendar: parses every .ics file saved in DATA_DIR/ics_exports/ and
                  restores events to calendar.json without re-calling the AI.
        """
        log.info("Command 'refreshdb' by %s", ctx.author)

        if self.storage.list_bills():
            await ctx.send(
                "❌ Database is not empty. `!refreshdb` only runs on a fresh/empty data store.\n"
                "Remove all bills first with `!removebill` or clear the data file directly."
            )
            return

        await ctx.send("🔄 Starting database refresh...")

        # ------------------------------------------------------------------
        # Part 1 — Bills: scan command channel history for !addbill commands
        # ------------------------------------------------------------------
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            await ctx.send("❌ Command channel not found.")
            return

        await ctx.send("📜 Scanning channel history for `!addbill` commands...")
        bill_numbers: list[str] = []
        async for message in channel.history(limit=None, oldest_first=True):
            if message.author.bot:
                continue
            match = re.match(r"^!addbill\s+([A-Za-z0-9]+)", message.content.strip(), re.IGNORECASE)
            if match:
                num = match.group(1).upper()
                if num not in bill_numbers:
                    bill_numbers.append(num)

        log.info("refreshdb: found %d unique !addbill command(s) in history", len(bill_numbers))

        added_bills, failed_bills = [], []
        async with ctx.typing():
            for bill_number in bill_numbers:
                key = self.storage.bill_key(bill_number)
                try:
                    result = await self.client.search_bill(bill_number)
                except LegiScanError as exc:
                    log.error("refreshdb: LegiScan search error for %s: %s", bill_number, exc)
                    failed_bills.append(bill_number)
                    continue
                if not result:
                    failed_bills.append(bill_number)
                    continue
                try:
                    full = await self.client.get_bill(result["bill_id"])
                except LegiScanError as exc:
                    log.error("refreshdb: LegiScan fetch error for %s: %s", bill_number, exc)
                    failed_bills.append(bill_number)
                    continue
                now = datetime.datetime.now().isoformat()
                record = {
                    **full,
                    "added_at": now,
                    "added_by": f"refreshdb (originally from channel history)",
                    "last_checked": now,
                    "last_alerted_action_date": full.get("last_action_date", ""),
                }
                self.storage.save_bill(key, record)
                added_bills.append(bill_number)
                log.info("refreshdb: restored bill %s", bill_number)

        bill_lines = []
        if added_bills:
            bill_lines.append(f"✅ Bills restored ({len(added_bills)}): {', '.join(added_bills)}")
        if failed_bills:
            bill_lines.append(f"❌ Bills failed ({len(failed_bills)}): {', '.join(failed_bills)}")
        if not bill_numbers:
            bill_lines.append("ℹ️ No `!addbill` commands found in channel history.")
        await ctx.send("\n".join(bill_lines) if bill_lines else "ℹ️ No bills to restore.")

        # ------------------------------------------------------------------
        # Part 2 — Calendar: parse saved .ics exports
        # ------------------------------------------------------------------
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        ics_dir = data_dir / "ics_exports"
        ics_files = sorted(ics_dir.glob("*.ics")) if ics_dir.exists() else []

        if not ics_files:
            await ctx.send("ℹ️ No saved ICS exports found — calendar not restored.")
            log.info("refreshdb: no ICS files in %s", ics_dir)
        else:
            await ctx.send(f"📅 Restoring calendar from {len(ics_files)} ICS export(s)...")
            restored_events: list[dict] = []
            for ics_path in ics_files:
                try:
                    cal = icalendar.Calendar.from_ical(ics_path.read_bytes())
                    for component in cal.walk():
                        if component.name != "VEVENT":
                            continue
                        uid = str(component.get("UID", ""))
                        title = str(component.get("SUMMARY", "Event"))
                        dtstart = component.get("DTSTART")
                        dtend = component.get("DTEND")
                        if not dtstart or not uid:
                            continue
                        dt = dtstart.dt
                        if isinstance(dt, datetime.datetime):
                            date_str = dt.date().isoformat()
                            start_time = dt.strftime("%H:%M")
                        else:
                            date_str = dt.isoformat()
                            start_time = "00:00"
                        end_time = None
                        if dtend:
                            et = dtend.dt
                            if isinstance(et, datetime.datetime):
                                end_time = et.strftime("%H:%M")
                        restored_events.append({
                            "uid": uid,
                            "date": date_str,
                            "day_name": datetime.date.fromisoformat(date_str).strftime("%A"),
                            "title": title,
                            "start_time": start_time,
                            "end_time": end_time,
                            "user": "restored",
                            "alerted": False,
                        })
                except Exception as exc:
                    log.error("refreshdb: failed to parse %s: %s", ics_path.name, exc)

            calendar_storage = self.bot.storage_manager.get("calendar")
            calendar_storage.restore_events(restored_events)
            log.info("refreshdb: restored %d calendar event(s) from %d ICS file(s)", len(restored_events), len(ics_files))
            await ctx.send(f"✅ Calendar restored — {len(restored_events)} event(s) from {len(ics_files)} ICS file(s).")

        log.info(
            "refreshdb complete — bills: added=%d failed=%d | calendar_events=%d",
            len(added_bills), len(failed_bills),
            len(restored_events) if ics_files else 0,
        )

    @commands.command(name="billreport")
    async def bill_report(self, ctx: commands.Context, *, target: str = "all") -> None:
        """
        Generate and upload an Excel report.

        Usage:
          !billreport          — all tracked bills
          !billreport all      — all tracked bills
          !billreport S1234    — single bill
          !billreport S1,A567  — comma-separated subset
        """
        target = target.strip()
        log.info("Command 'billreport' by %s — target=%r", ctx.author, target)

        async with ctx.typing():
            all_bills = self.storage.list_bills()
            if not all_bills:
                await ctx.send("No bills are being tracked.")
                return

            today = datetime.date.today().strftime("%m%d%y")

            if target.lower() == "all":
                xlsx_bytes = build_all_report(all_bills)
                filename = f"bills_report_{today}.xlsx"
            elif "," in target:
                numbers = [n.strip().upper() for n in target.split(",")]
                subset = [b for b in all_bills if b.get("bill_number", "").upper() in numbers]
                if not subset:
                    await ctx.send("None of those bill numbers are being tracked.")
                    return
                xlsx_bytes = build_all_report(subset)
                tag = "_".join(numbers)[:40]
                filename = f"bills_report_{tag}_{today}.xlsx"
            else:
                bill_number = target.upper()
                key = self.storage.bill_key(bill_number)
                bill = self.storage.get_bill(key)
                if not bill:
                    await ctx.send(f"`{bill_number}` is not being tracked.")
                    return
                xlsx_bytes = build_single_report(bill)
                filename = f"{bill_number}_report_{today}.xlsx"

        reports_channel = self.bot.get_channel(self.reports_channel_id)
        if not reports_channel:
            log.warning("Bills reports channel (CH_MBT_REPORTS_ID=%d) not found", self.reports_channel_id)
            await ctx.send("Error: reports channel not found.")
            return

        await reports_channel.send(
            file=discord.File(io.BytesIO(xlsx_bytes), filename=filename)
        )
        log.info("Bills report: '%s' uploaded to reports channel by %s", filename, ctx.author)
