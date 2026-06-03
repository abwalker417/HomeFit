# AI Stack Overview

## Current API Usage

### Anthropic (Claude API)
| App | Model | Purpose | Trigger |
|-----|-------|---------|---------|
| SparkyFitness | claude-sonnet-4-6 | AI coach chat — food logging, workout advice, Q&A | User opens AI chat in Sparky |

**Cost driver:** Per-conversation token usage. Each chat message = input + output tokens billed.

---

### OpenAI
| App | Model | Purpose | Trigger |
|-----|-------|---------|---------|
| RecipeForge | whisper-1 | Audio transcription — extracts speech from cooking videos | Every URL or file import |
| RecipeForge | gpt-4o | Recipe parsing — converts transcription/image into structured recipe JSON | Every URL or file import |
| Mealie | whisper-1 | Audio transcription — for video URL recipe imports | User pastes a video URL |
| Mealie | gpt-4o | Recipe scraping fallback — parses page HTML when standard scraper fails | Failed URL scrape |
| Mealie | gpt-4o (vision) | Image recipe extraction — reads recipe from a photo | User uploads recipe image |

**Cost driver:** Whisper is charged per audio minute (~$0.006/min). GPT-4o is charged per token. A typical RecipeForge video import costs ~$0.02–0.05 total.

---

## Ollama Feasibility

Ollama runs open-source LLMs locally on your Proxmox server. No per-call cost, fully private.

| Current Usage | Replaceable with Ollama? | Notes |
|--------------|--------------------------|-------|
| Sparky AI coach (Claude) | ✅ Yes | Sparky supports custom base URL — point to Ollama's OpenAI-compatible endpoint |
| RecipeForge recipe parsing (GPT-4o text) | ✅ Yes | Switch `model` and `base_url` in app.py |
| RecipeForge image extraction (GPT-4o vision) | ⚠️ Partial | Needs a vision-capable model (LLaVA, llama3.2-vision). Quality lower than GPT-4o |
| RecipeForge/Mealie audio transcription (Whisper) | ❌ No | Whisper is a specialized audio model. Ollama doesn't run Whisper. Keep OpenAI for this only |
| Mealie scraping fallback (GPT-4o text) | ✅ Yes | Mealie explicitly supports Ollama as a provider |
| Mealie image extraction (GPT-4o vision) | ⚠️ Partial | Same as above — needs vision model |

**Recommended hybrid approach:**
- Ollama → all text generation (Sparky coach, recipe parsing)
- OpenAI Whisper → audio transcription only (no local alternative)
- Ollama vision model → image recipe extraction (acceptable quality tradeoff)

---

## Recommended Ollama Models

| Use Case | Model | Size | Notes |
|----------|-------|------|-------|
| General chat / coaching | llama3.1:8b | 4.7GB | Fast, good quality for Q&A and advice |
| Recipe parsing (text) | llama3.1:8b | 4.7GB | Same model, structured JSON output works well |
| Image extraction (vision) | llama3.2-vision:11b | 7.9GB | Best local vision option currently |
| Higher quality (if hardware allows) | llama3.1:70b | 40GB | Closer to GPT-4o quality, needs strong GPU/RAM |

**Hardware note:** Your Proxmox server needs enough RAM. llama3.1:8b needs ~6GB RAM, 11b vision needs ~10GB. Check your server specs before committing.

---

## Future: HomeFit Smart Coach

A coach built into HomeFit would have access to rich context that generic AI doesn't:

- Current fitness level, limitations, and equipment
- Full workout history (exercises, sets, reps, weight progression)
- Weight trend over time
- Ignored/preferred exercises
- Weekly schedule and rest patterns

**What it could do:**
- Suggest weight increases based on progression ("you've hit 3×12 at 25lbs three times — try 30lbs")
- Flag overtraining or missed rest days
- Recommend exercise swaps based on limitations
- Answer questions about form or programming

**Implementation path:**
- Add a `/coach` chat endpoint in HomeFit that injects user profile + recent history into the system prompt
- Use Ollama (llama3.1:8b) for the LLM call — no API cost, private
- Simple chat UI in the PWA
- For HomeFit iOS, surface it natively once the app is built out

**Ollama is the right choice here** — the coach needs user data that you wouldn't want sent to a third-party API anyway.
