"""
Telegram bot front-end for the LaTeX resume tailoring agent.

Flow:
  /start  -> ask for the resume as a .tex (plus any .cls/.sty it needs)
  resume  -> collect files, then ask for the job description
  JD      -> analyze, edit the LaTeX, compile, return PDF + edited .tex + redline
  /cancel -> reset

Run:  python bot.py   (needs TELEGRAM_BOT_TOKEN + the provider key, and a LaTeX
engine on the machine — tectonic recommended, or a TeX Live install.)
"""

import logging
import os
import shutil
import tempfile

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import analyzer
import resume_builder

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("resume_bot")

ASK_RESUME, ASK_JD = range(2)
SUPPORT_EXTS = (".tex", ".cls", ".sty", ".bib")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _download_to(doc, dest_path):
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(dest_path)


def _authorized(update: Update) -> bool:
    allowed = os.environ.get("ALLOWED_USER_ID", "").strip()
    return not allowed or str(update.effective_user.id) == allowed


def _cleanup_workdir(user_data):
    wd = user_data.pop("workdir", None)
    if wd and os.path.isdir(wd):
        shutil.rmtree(wd, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await update.message.reply_text("Sorry, this is a private bot.")
        return ConversationHandler.END
    _cleanup_workdir(context.user_data)
    context.user_data.clear()
    context.user_data["workdir"] = tempfile.mkdtemp(prefix="resume_")
    await update.message.reply_text(
        "Hi! I tailor your LaTeX resume to a job description and compile it.\n\n"
        "Step 1/2: send your resume as a .tex file. If it uses a custom .cls/.sty "
        "template, send those first, then the .tex."
    )
    return ASK_RESUME


async def need_resume_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send your resume as a .tex file (LaTeX source).")
    return ASK_RESUME


async def receive_resume_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    fname = os.path.basename(doc.file_name or "file")
    ext = next((s for s in SUPPORT_EXTS if fname.lower().endswith(s)), None)
    if ext is None:
        await update.message.reply_text(
            "Send your resume as a .tex (LaTeX source). If it uses a custom .cls/.sty, send that too."
        )
        return ASK_RESUME

    dest = os.path.join(context.user_data["workdir"], fname)
    await _download_to(doc, dest)

    if ext == ".tex":
        with open(dest, encoding="utf-8", errors="ignore") as fh:
            context.user_data["resume_source"] = fh.read()
        context.user_data["main_tex"] = fname
        await update.message.reply_text(
            "Resume received.\n\nStep 2/2: paste the job description (or send it as a PDF/DOCX/TXT)."
        )
        return ASK_JD

    await update.message.reply_text(f"Saved {fname}. Send any other support files, then your main .tex.")
    return ASK_RESUME


async def _read_jd(update, context):
    """Return the JD text, or None if it needs re-sending (a reply is sent on failure)."""
    if update.message.document:
        fname = (update.message.document.file_name or "").lower()
        suffix = next((s for s in (".pdf", ".docx", ".txt") if fname.endswith(s)), None)
        if suffix is None:
            await update.message.reply_text("Unsupported file. Paste the JD as text or send PDF/DOCX/TXT.")
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False).name
        try:
            await _download_to(update.message.document, tmp)
            jd_text, _ = analyzer.extract_text_from_resume(tmp)
        finally:
            os.unlink(tmp)
        return jd_text
    return update.message.text or ""


async def receive_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jd_text = await _read_jd(update, context)
    if jd_text is None:
        return ASK_JD
    if len(jd_text.split()) < 20:
        await update.message.reply_text("That job description looks short — paste the full text, please.")
        return ASK_JD

    source = context.user_data["resume_source"]
    await update.message.reply_text("Analyzing... this takes a few seconds.")
    try:
        llm = analyzer.analyze_with_llm(source, jd_text)
    except Exception as e:
        log.exception("analysis failed")
        await update.message.reply_text(f"Analysis failed: {e}\n(Check your API key and LLM_MODEL.)")
        _cleanup_workdir(context.user_data)
        return ConversationHandler.END

    for chunk in analyzer.split_message(analyzer.build_report(llm)):
        await update.message.reply_text(chunk)

    await _build_and_send_latex(update, context.user_data, jd_text)

    _cleanup_workdir(context.user_data)
    await update.message.reply_text("Done. Send /start to tailor for another job.")
    return ConversationHandler.END


async def _build_and_send_latex(update, user_data, jd_text):
    """Tailor the LaTeX, compile it, and send PDF + edited .tex + redline."""
    workdir = user_data["workdir"]
    source = user_data["resume_source"]
    stem = os.path.splitext(user_data["main_tex"])[0]
    edited_name = f"{stem}_tailored.tex"
    edited_path = os.path.join(workdir, edited_name)
    diff_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name

    await update.message.reply_text("Tailoring your LaTeX and compiling...")
    try:
        edited = resume_builder.tailor_latex(source, jd_text)
        with open(edited_path, "w", encoding="utf-8") as fh:
            fh.write(edited)

        pdf_path, clog = resume_builder.compile_latex(edited_name, workdir)
        resume_builder.make_redline_pdf(source, edited, diff_pdf)

        with open(edited_path, "rb") as f:
            await update.message.reply_document(f, filename="tailored_resume.tex")
        with open(diff_pdf, "rb") as f:
            await update.message.reply_document(f, filename="changes_redline.pdf")

        if pdf_path:
            with open(pdf_path, "rb") as f:
                await update.message.reply_document(f, filename="tailored_resume.pdf")
            qa = resume_builder.qa_compiled(pdf_path)
            lines = ["QA check:"]
            lines += [f"  [ok] {g}" for g in qa["good"]]
            lines += [f"  [! ] {i}" for i in qa["issues"]]
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "It didn't compile. The edited .tex is attached — drop it into Overleaf, "
                "or fix the issue flagged below (usually an unescaped special character):\n\n"
                + resume_builder.error_tail(clog)
            )
    except Exception as e:
        log.exception("latex build failed")
        await update.message.reply_text(f"Couldn't tailor/compile: {e}")
    finally:
        try:
            os.unlink(diff_pdf)
        except OSError:
            pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup_workdir(context.user_data)
    context.user_data.clear()
    await update.message.reply_text("Cancelled. /start to begin again.")
    return ConversationHandler.END


async def nudge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send /start to begin tailoring your resume.")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong handling that. Check the terminal, then /start to retry."
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (get one from @BotFather).")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    key_var = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    if not os.environ.get(key_var):
        log.warning("%s is not set — analysis will fail until you add it.", key_var)
    if not (shutil.which("tectonic") or shutil.which("latexmk") or shutil.which("pdflatex")):
        log.warning("No LaTeX engine found. Install tectonic (brew install tectonic) to compile PDFs.")

    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_RESUME: [
                MessageHandler(filters.Document.ALL, receive_resume_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, need_resume_file),
            ],
            ASK_JD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_jd),
                MessageHandler(filters.Document.ALL, receive_jd),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, nudge))
    app.add_error_handler(on_error)
    log.info("Using LLM: %s / %s", *analyzer.active_provider_model())
    log.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()