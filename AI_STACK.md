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

### Ollama (Local — MacBook Air M4, 192.168.68.56:11434)
| App | Model | Purpose | Trigger |
|-----|-------|---------|---------|
| HomeFit Apex | llama3.1:8b | AI workout generation — personalised based on history, goal, duration | Any workout tile tap |
| HomeFit Apex | llama3.1:8b | Coach chat — form tips, progression advice, Q&A | Apex floating panel |
| HomeFit Apex | llama3.1:8b | Post-workout insights + progressive overload suggestions | Workout completion |

**Cost:** $0 — runs locally on the M4. Private — no user data leaves the network.

---

## Ollama Migration Status

| Current Usage | Migrated to Ollama? | Notes |
|--------------|---------------------|-------|
| HomeFit Apex coach | ✅ Done | llama3.1:8b, fully local |
| Sparky AI coach (Claude) | ❌ Pending | Sparky supports custom base URL — can point to Ollama |
| RecipeForge recipe parsing (GPT-4o text) | ❌ Pending | Simple code change in app.py |
| RecipeForge/Mealie audio transcription (Whisper) | ❌ No path | Whisper is a specialized audio model — keep OpenAI for this only |
| Mealie scraping fallback (GPT-4o text) | ❌ Pending | Mealie supports Ollama natively as a provider |
| Image recipe extraction (GPT-4o vision) | ⚠️ Partial | llama3.2-vision:11b available but lower quality |

**Recommended next migration:** Sparky AI coach → Ollama (just update base URL in Sparky settings)

---

## Ollama Setup

- **Host:** MacBook Air M4, `192.168.68.56:11434` (ethernet, DHCP reserved)
- **Model:** `llama3.1:8b` (4.9GB)
- **Network binding:** `OLLAMA_HOST=0.0.0.0` set via launchctl
- **Auto-start:** Ollama.app set to launch at login

---

## HomeFit Apex Coach — Architecture

```
User taps workout icon
        ↓
app.py build_day() → coach.is_available()?
        ↓ yes                    ↓ no
coach.generate_workout()    build_workout() (rule-based)
        ↓
  Ollama llama3.1:8b
  (profile + history + exercise library as context)
        ↓
  Structured JSON workout
        ↓
  Enriched with exercise library data
        ↓
  Saved to session → workout screen
```

**Context injected into every Apex call:**
- Fitness level, goal (weight loss/muscle/toning/general), target duration
- Current & goal weight, weight trend
- Equipment, limitations, ignored exercises
- Last 5 workouts with exercises and logged weights
- Exercise history for progressive overload detection

---

## Recommended Models

| Use Case | Model | Size |
|----------|-------|------|
| General chat / coaching | llama3.1:8b | 4.9GB |
| Recipe parsing (text) | llama3.1:8b | 4.9GB |
| Image extraction (vision) | llama3.2-vision:11b | 7.9GB |
| Higher quality (future) | llama3.1:70b | 40GB |
