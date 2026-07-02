"""
VuTune — IMVU Music Bot
Main Entry Point

Architecture:
  - asyncio event loop runs in a background thread (bot + room polling)
  - Flask dashboard runs in the main thread
  - They communicate via thread-safe calls + run_coroutine_threadsafe
"""

import asyncio
import threading
import logging
import sys

from utils.config import load_config, save_config
from bot.room_manager import RoomManager
from dashboard.app import create_app

# ── Logging setup ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s — %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('vutune.log', encoding='utf-8'),
    ]
)
logger = logging.getLogger('main')


def run_bot(room_manager: RoomManager, loop: asyncio.AbstractEventLoop):
    """Run the async bot in a dedicated thread with its own event loop."""
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(room_manager.start())
    except Exception as e:
        logger.error(f"Bot loop crashed: {e}")


def main():
    config = load_config()

    # Update config with bot credentials (already set in config.json)
    # VuTune / VuTune@1908! is stored in config.json

    # Create a dedicated asyncio loop for the bot
    bot_loop = asyncio.new_event_loop()

    # Create the shared room manager
    room_manager = RoomManager(config, bot_loop)

    # Start the bot in a background thread
    bot_thread = threading.Thread(
        target=run_bot,
        args=(room_manager, bot_loop),
        daemon=True,
        name='BotThread',
    )
    bot_thread.start()
    logger.info("Bot thread started.")

    # Create Flask app
    app = create_app(config, room_manager, bot_loop)

    print()
    print("=" * 50)
    print("   VuTune v1.0 - IMVU Music Bot")
    print("=" * 50)
    print(f"   Dashboard  -->  http://localhost:{config['dashboard']['port']}")
    print(f"   Stream URL -->  http://localhost:{config['icecast']['port']}{config['icecast']['mount']}")
    print("=" * 50)
    print()
    print("   Open the Dashboard in your browser to manage rooms.")
    print("   Press Ctrl+C to stop.\n")

    # Flask runs in main thread (blocking)
    app.run(
        host='0.0.0.0',
        port=config['dashboard']['port'],
        debug=False,
        use_reloader=False,
    )


if __name__ == '__main__':
    main()
