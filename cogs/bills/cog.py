"""
Bills Cog — track NJ Legislature bills and send @everyone alerts on updates.

Commands (prefix !):
  !addbill <bill_number>           — start tracking a bill (e.g. !addbill S1234)
  !removebill <bill_number>        — stop tracking a bill
  !listbills                       — show all tracked bills with current status
  !checkbills                      — force an immediate update poll
  !billreport <bill_number|all>    — upload Excel report
    e.g. !billreport S1234
         !billreport all
         !billreport S1234,A567    (comma-separated subset)

Environment variables:
  CH_BILLS_CHANNEL_ID   Discord channel ID for @everyone update alerts
  LEGISCAN_API_KEY      LegiScan API key (free at legiscan.com)
  BILLS_POLL_HOURS      How often to poll for updates (default: 6)
"""

from __future__ import annotations

import datetime
import io
import logging
import os

import discord
from discord.ext import commands, tasks

from . import storage
from .legiscan import LegiScanClient, LegiScanError
from .reporter import build_all_report, build_single_report

log = logging.getLogger("homebot.bills")


class BillsCog(commands.Cog):
    """Tracks NJ Legislature bills and sends alerts when they are updated."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        raw_ch = os.environ.get("CH_BILLS_CHANNEL_ID")
        if not raw_ch:
            raise RuntimeError("CH_BILLS_CHANNEL_ID is not set in the environment.")
        try:
            self.channel_id = int(raw_ch)
        except ValueError:
            raise RuntimeError(f"CH_BILLS_CHANNEL_ID must be an integer, got: {raw_ch!r}")

        api_key = os.environ.get("LEGISCAN_API_KEY")
        if not api_key:
            raise RuntimeError("LEGISCAN_API_KEY is not set in the environment.")
        self.client = LegiScanClient(api_key)

        poll_hours = int(os.environ.get("BILLS_POLL_HOURS", "6"))
        self._poll.change_interval(hours=poll_hours)
        self._poll.start()
        log.info("Bills cog started — polling every %d hour(s)", poll_hours)

    def cog_unload(self) -> None:
        self._poll.cancel()

    # ------------------------------------------------------------------
    # Background poll
    # ------------------------------------------------------------------

    @tasks.loop(hours=6)
    async def _poll(self) -> None:
        """Fetch LegiScan master list, detect changed bills, send alerts."""
        bills = storage.list_bills()
        if not bills:
            return

        log.info("Polling LegiScan for %d tracked bill(s)...", len(bills))
        try:
            master = await self.client.get_master_list()
        except LegiScanError as exc:
            log.error("Failed to fetch master list: %s", exc)
            return

        for bill in bills:
            bill_number = bill.get("bill_number", "")
            state = bill.get("state", "NJ")
            key = storage.bill_key(bill_number, state)

            entry = master.get(bill_number)
            if not entry:
                log.debug("Bill %s not found in master list", bill_number)
                continue

            if entry["change_hash"] == bill.get("change_hash", ""):
                log.debug("No change for %s", bill_number)
                continue

            # Change detected — fetch full bill details
            try:
                updated_data = await self.client.get_bill(entry["bill_id"])
            except LegiScanError as exc:
                log.error("Failed to fetch bill %s: %s", bill_number, exc)
                continue

            # Merge fetched data into stored bill (preserve added_by, added_at, etc.)
            merged = {**bill, **updated_data}
            merged["last_checked"] = datetime.datetime.now().isoformat()

            new_action_date = updated_data.get("last_action_date", "")
            alerted_date = bill.get("last_alerted_action_date", "")

            if new_action_date and new_action_date > alerted_date:
                await self._send_alert(merged)
                merged["last_alerted_action_date"] = new_action_date

            storage.save_bill(key, merged)
            log.info("Updated %s — new action: %s", bill_number, updated_data.get("last_action"))

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_alert(self, bill: dict) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            log.warning("Bills alert channel %d not found", self.channel_id)
            return

        await channel.send(
            f"@everyone\n"
            f"📋 **BILL UPDATE:** {bill['bill_number']} — {bill['title']}\n"
            f"**New Action:** {bill['last_action']}\n"
            f"**Date:** {bill['last_action_date']}\n"
            f"**Status:** {bill['status']}\n"
            f"🔗 {bill['url']}"
        )
        log.info("Sent update alert for %s", bill["bill_number"])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="addbill")
    async def add_bill(self, ctx: commands.Context, bill_number: str) -> None:
        """Start tracking a bill. Usage: !addbill S1234"""
        bill_number = bill_number.upper()
        key = storage.bill_key(bill_number)

        if storage.get_bill(key):
            await ctx.send(f"`{bill_number}` is already being tracked.")
            return

        async with ctx.typing():
            try:
                result = await self.client.search_bill(bill_number)
            except LegiScanError as exc:
                log.error("LegiScan search error: %s", exc)
                await ctx.send(f"Error searching for `{bill_number}`: {exc}")
                return

        if not result:
            await ctx.send(f"Bill `{bill_number}` not found on LegiScan.")
            return

        # Fetch full details
        try:
            full = await self.client.get_bill(result["bill_id"])
        except LegiScanError as exc:
            log.error("LegiScan fetch error: %s", exc)
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
        storage.save_bill(key, record)
        log.info("%s added bill %s", ctx.author, bill_number)

        embed = discord.Embed(
            title=f"✅ Now tracking {bill_number}",
            description=full.get("title", ""),
            color=discord.Color.green(),
        )
        embed.add_field(name="Status", value=full.get("status", "—"))
        embed.add_field(name="Last Action", value=full.get("last_action", "—"), inline=False)
        embed.add_field(name="URL", value=full.get("url", "—"), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="removebill")
    async def remove_bill(self, ctx: commands.Context, bill_number: str) -> None:
        """Stop tracking a bill. Usage: !removebill S1234"""
        bill_number = bill_number.upper()
        key = storage.bill_key(bill_number)

        if not storage.get_bill(key):
            await ctx.send(f"`{bill_number}` is not being tracked.")
            return

        storage.remove_bill(key)
        log.info("%s removed bill %s", ctx.author, bill_number)
        await ctx.send(f"❌ Stopped tracking `{bill_number}`.")

    @commands.command(name="listbills")
    async def list_bills(self, ctx: commands.Context) -> None:
        """Show all currently tracked bills and their status."""
        bills = storage.list_bills()
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

    @commands.command(name="checkbills")
    async def check_bills(self, ctx: commands.Context) -> None:
        """Force an immediate update poll for all tracked bills."""
        await ctx.send("🔄 Checking for bill updates...")
        await self._poll()
        await ctx.send("✅ Done checking.")

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

        async with ctx.typing():
            all_bills = storage.list_bills()
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
                key = storage.bill_key(bill_number)
                bill = storage.get_bill(key)
                if not bill:
                    await ctx.send(f"`{bill_number}` is not being tracked.")
                    return
                xlsx_bytes = build_single_report(bill)
                filename = f"{bill_number}_report_{today}.xlsx"

        await ctx.send(
            file=discord.File(io.BytesIO(xlsx_bytes), filename=filename)
        )
        log.info("Report '%s' sent to %s", filename, ctx.author)
