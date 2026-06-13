# Resume Tailoring Telegram Bot

A Telegram bot that tailors a LaTeX resume to a job description. It returns a
match report, edits the resume source, compiles a tailored PDF, and sends a
redline PDF showing what changed.

## What It Does

- Accepts a `.tex` resume, plus optional `.cls`, `.sty`, or `.bib` support files.
- Accepts a job description as pasted text, PDF, DOCX, or TXT.
- Produces a structured resume-vs-job analysis with missing keywords, rewrite
  suggestions, and honest gaps.
- Edits the LaTeX source while preserving the template and formatting commands.
- Compiles the tailored resume to PDF when a LaTeX engine is installed.
- Sends back the edited `.tex`, a redline PDF, and the compiled tailored PDF.

## Project Structure

- `bot.py` - Telegram conversation flow and file handling.
- `analyzer.py` - resume/job analysis, ATS checks, and provider-agnostic LLM calls.
- `resume_builder.py` - LaTeX tailoring, compilation, PDF QA, and redline output.
- `.env.example` - environment variable template.
- `requirements.txt` - Python dependencies.

## Requirements

- Python 3.10+
- A Telegram bot token from `@BotFather`
- An Anthropic or OpenAI API key
- Optional but recommended: a LaTeX engine for PDF compilation
  - `tectonic` is preferred
  - `latexmk` with TeX Live also works

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set:

```bash
TELEGRAM_BOT_TOKEN=your-telegram-token
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-anthropic-key
```

For OpenAI instead:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-key
```

## Run

```bash
python bot.py
```

Open your Telegram bot and send `/start`.

## Bot Flow

1. Send any support files first, such as `.cls`, `.sty`, or `.bib`.
2. Send the main `.tex` resume file.
3. Paste the job description or upload it as PDF, DOCX, or TXT.
4. Receive the analysis report, tailored source, redline PDF, and compiled PDF.

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Token from `@BotFather`. |
| `ALLOWED_USER_ID` | No | Restrict bot usage to one Telegram user ID. |
| `LLM_PROVIDER` | Yes | `anthropic` or `openai`. Defaults to `anthropic`. |
| `ANTHROPIC_API_KEY` | Provider-specific | Required when using Anthropic. |
| `OPENAI_API_KEY` | Provider-specific | Required when using OpenAI. |
| `LLM_MODEL` | No | Override the provider default model from `analyzer.py`. |

## Notes

- The real `.env` file is intentionally ignored by git. Do not commit API keys
  or Telegram tokens.
- The prompt instructs the model not to invent experience, tools, employers,
  dates, or metrics. It should only reframe supported resume content.
- If PDF compilation fails, the bot still sends the edited `.tex` and the LaTeX
  error tail so you can fix it locally or in Overleaf.

## Deploy

The bot uses Telegram long polling, so it can run on any always-on host such as
a VPS, Railway, Render, or Fly.io. Configure the same environment variables on
the host before starting `python bot.py`.
