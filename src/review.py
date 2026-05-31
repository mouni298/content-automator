"""Telegram review gate.

  send_for_review(video_id)  - called by the pipeline; sends the MP4 with
                               Approve / Reject inline buttons.
  python -m src.review       - runs the long-lived bot that listens for button
                               taps and publishes approved videos.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from telegram.ext import (Application, CallbackQueryHandler, ContextTypes,
                          MessageHandler, filters)

from .config import env, cfg
from . import db, runner, feedback, series, reach
from .topic_picker import Topic
from .publish import youtube


def _bot() -> Bot:
    return Bot(token=env("TELEGRAM_BOT_TOKEN", required=True))


async def _send(video_id: int):
    row = db.get_video(video_id)
    chat_id = env("TELEGRAM_CHAT_ID", required=True)
    caption = (f"🎬 *{row['topic']}*\n\n{row['caption']}\n\n"
               f"Approve to publish to YouTube?")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{video_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{video_id}"),
    ]])
    with open(row["video_path"], "rb") as f:
        await _bot().send_video(chat_id=chat_id, video=f, caption=caption,
                                parse_mode="Markdown", reply_markup=kb)


def send_for_review(video_id: int):
    """Synchronous entry point for the pipeline."""
    asyncio.run(_send(video_id))


async def _on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass   # tap may have expired ("query is too old"); proceed anyway
    action, vid = q.data.split(":")
    video_id = int(vid)
    row = db.get_video(video_id)

    if action == "reject":
        # enter "awaiting feedback" state for this chat; the next text reply
        # drives regeneration (or "skip" discards).
        context.chat_data["awaiting_feedback"] = video_id
        await q.edit_message_caption(
            caption=(f"❌ Rejected: {row['topic']}\n\nReply with feedback to "
                     f"regenerate (e.g. “make it shorter”, “wrong images, focus on "
                     f"the battle”), or send “skip” to discard."))
        return

    await q.edit_message_caption(caption=f"⏫ Connecting to YouTube: {row['topic']} …")
    try:
        channel = youtube.channel_title()   # first run opens the OAuth browser login
        await q.edit_message_caption(
            caption=f"⏫ Publishing to “{channel}”: {row['topic']} …")
        part = row["part_no"]
        title = row["topic"] + (f" — Part {part}" if part else "") + " #Shorts"
        extra_tags = reach.trending_tags()   # researched niche tags (best-effort)
        if extra_tags:
            print(f"  [reach] +tags: {extra_tags}")
        yt_id = youtube.upload(
            Path(row["video_path"]),
            title=title,
            description=row["caption"] or "",
            extra_tags=extra_tags,
        )
        db.update_video(video_id, status="published", youtube_id=yt_id)
        # advance the series continuity (best-effort) once a part is approved
        if row["series_id"]:
            iso_now = datetime.now(timezone.utc).isoformat()
            series.update_summary(row["series_id"], row["script"] or "", iso_now=iso_now)
        url = f"https://youtube.com/shorts/{yt_id}"
        await q.edit_message_caption(
            caption=f"✅ Published to “{channel}”: {title}\n{url}")
    except Exception as e:  # surface failures back to the chat
        db.update_video(video_id, status="failed", error=str(e))
        await q.edit_message_caption(caption=f"⚠️ Publish failed: {row['topic']}\n{e}")


async def _on_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text reply after a Reject → regenerate the video with this feedback."""
    video_id = context.chat_data.pop("awaiting_feedback", None)
    if video_id is None:
        return   # not awaiting feedback; ignore stray messages
    text = (update.message.text or "").strip()
    row = db.get_video(video_id)

    if text.lower() == "skip":
        db.update_video(video_id, status="rejected")
        await update.message.reply_text(f"Discarded: {row['topic']}")
        return

    max_regens = cfg().get("feedback", {}).get("max_regens", 2)
    regen = row["regen_count"] or 0
    if regen >= max_regens:
        db.update_video(video_id, status="rejected", feedback=text)
        await update.message.reply_text(
            f"Max regenerations ({max_regens}) reached for “{row['topic']}”. Marked rejected.")
        return

    scope = feedback.classify(text)
    db.update_video(video_id, feedback=text, regen_count=regen + 1)
    await update.message.reply_text(f"🔄 Regenerating ({scope}) per: “{text}” …")

    topic = Topic(row["topic"], "", row["wikipedia_title"])
    iso_now = datetime.now(timezone.utc).isoformat()
    try:
        # run off the event loop (own loop in the worker thread) so the bot stays responsive
        await asyncio.to_thread(
            runner.run_regeneration, video_id, topic,
            feedback=text, iso_now=iso_now, reuse_images=(scope == "wording"))
    except Exception as e:
        db.update_video(video_id, status="failed", error=str(e))
        await update.message.reply_text(f"⚠️ Regeneration failed: {e}")
        return

    await _send(video_id)   # fresh review message with the regenerated video


def run_listener():
    app = Application.builder().token(env("TELEGRAM_BOT_TOKEN", required=True)).build()
    app.add_handler(CallbackQueryHandler(_on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_feedback))
    print("Review bot listening. Press Ctrl+C to stop.")
    # drop_pending_updates: ignore taps queued before startup (they're stale)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_listener()
