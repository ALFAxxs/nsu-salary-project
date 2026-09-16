"""Run the Telegram bot as a Django management command (uses current settings)."""
import asyncio

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the aiogram Telegram bot (long polling)."

    def handle(self, *args, **options):
        from apps.telegram_bot.bot import main
        asyncio.run(main())
