# ManualMate

A polished, self-contained college-review demo for following an appliance manual. Built with Streamlit and Python; no API key or account is needed.

## Run on Windows

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During setup, enable **Add Python to PATH** (or install the Python Launcher).
2. Extract this folder if it came in a ZIP.
3. Double-click **`run.bat`**. The first launch creates a local environment and installs the listed packages. Keep the terminal window open.
4. Open the local address printed in the terminal (usually `http://localhost:8501`).

To start it later, double-click `run.bat` again. To stop it, close the terminal or press Ctrl+C.

## What the demo shows

- A built-in Northstar NDW-240 dishwasher manual, plus a PDF upload option.
- A parts, tools, and safety overview with expandable manual pages.
- Five guided setup steps with Back, Next, completion, and live progress.
- A step-aware **I’m stuck** flow for common setup problems.
- Question answering with manual page references. For uploaded PDFs, answers quote matching extracted passages. For the built-in sample, responses are predefined examples.
- An integrated help pipeline: questions go through the background worker, Agent B returns authenticated safety triage over TCP, the BDI planner selects the next safe intention, utility scoring ranks assistance, and buyer/supplier agents negotiate before a recommendation is shown.
- Uploaded text-based manuals are read using layout-aware PDF text extraction. A local parser identifies setup sections, joins wrapped numbered instructions, excludes later operation and troubleshooting sections, normalizes common measurement glyphs, and cites each step to its original PDF page. A separate section-aware parser turns labeled tool and parts lists into individual items with page citations.

## Agent assignments

The app stays local and needs no API key. Run from this folder using its virtual environment: `\.venv\Scripts\python.exe -m streamlit run app.py`.

- **2.1 Background worker:** Every submitted manual question is processed as a queued background job. The daemon thread runs a periodic monitor, and a lock protects shared latency records. Timings and failures are appended to `logs/manualmate.log`. The Agent Lab controls can also start/stop the monitor and queue a sample job.
- **2.2 TCP messaging:** Each help request sends the active question, manual name, and setup step as newline-delimited typed JSON over loopback TCP. Agent B classifies the issue and returns a safety note that is included in ManualMate's answer. The two-terminal scripts remain available for showing the raw socket exchange.
- **3.1 Negotiation:** Buyer and supplier agents negotiate across the eligible assistance options. Offers over budget or below the buyer's quality threshold are rejected; ManualMate recommends the option with the highest assistance utility after the agreed price. The answer includes the winning proposal timeline and consensus result.
- **3.2 Utility:** Each question contextually adjusts relevance for ten assistance options, then scores them with `U = 5R + 1.5T + 3S − 2P`, where relevance `R` and safety `S` are 1–10, saved time `T` is minutes, and price `P` is dollars. The buyer requires relevance ≥ 6 and safety ≥ 7, and the negotiated price must stay within $30. ManualMate uses the highest-utility agreed offer as its assistance recommendation.

- **4.1 BDI planner:** During setup and help requests, ManualMate keeps explicit beliefs (manual, current step, recall status, and Agent B safety category), a desire (the user's safe resolution goal), and an active intention list. New safety or recall beliefs trigger replanning. A leak switches the plan to stop, unplug, and close the tap; an electrical risk or possible recall takes higher priority.
- **4.2 Adaptive knowledge:** When the user checks **Official product recall notices**, ManualMate sends only the entered or extracted product/model name to the public CPSC Recalls REST API. It parses returned JSON into structured recall beliefs. A possible match pauses normal repair recommendations until the model is checked against the official notice. The lookup needs internet access but no API key; the PDF text stays local.
- **5.1 Message integrity:** The integrated TCP request and response can use HMAC-SHA256 authentication. Agent B checks the signature and permitted source host before processing a message; invalid messages are blocked and logged. This demo uses a shared 32-byte key. It demonstrates message integrity/authentication and host allowlisting; it is not a substitute for a production key-management system or TLS.
- **5.2 Unified lifecycle:** The normal Ask flow is the capstone cycle: queued request, manual answer, authenticated Agent B triage, optional recall knowledge, BDI replan, utility/negotiation, and a visible response with its current beliefs, goal, intention, and security status. These are part of the ManualMate flow, not assignment-only pages.

## Uploading a manual

Choose **Upload a PDF** in the sidebar, then select a text-based PDF. Open **01 Overview** to review the extracted tool and parts lists, then **02 Guided setup** to step through instructions found in its setup/installation sections. ManualMate matches embedded page images to nearby step text and uses an image at most once, so later steps do not repeat an earlier image. If no distinct image matches a step, it says so instead of showing the same image or the full page. Vector-only drawings are not extracted yet. Steps, inventory items, and safety notes show their source page. **Local parser** is the default and needs no key. To use Gemini, choose it and enable its per-PDF consent checkbox; only extracted text is sent, not the original PDF. The optional recall checkbox sends only a product/model query to CPSC. Verify generated instructions and citations against the manual page.

To enable Gemini, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add your `GEMINI_API_KEY`. The key may also be set in the environment. `secrets.toml` is excluded by `.gitignore`; never put a key in app source code or share it. ManualMate uses Gemini 3.5 Flash-Lite for optional structured setup-step extraction. Google currently lists a free tier for this model; quotas and availability can change. Without a configured key or network access, ManualMate continues using its local parser. Scanned image-only PDFs need OCR (not included); if no clear instructions are extracted, the app explains that instead of showing unrelated sample steps.

## Smoke checks

Run `\.venv\Scripts\python.exe -m unittest discover -s tests -v` from this folder. `logs/` is created at runtime; the app writes operational and latency records there.

## Optional multi-host Agent B

The app uses an authenticated loopback Agent B by default. For a second machine, set `MANUALMATE_AGENT_HMAC_KEY` to the same 64-character hexadecimal key on both machines, set `MANUALMATE_AGENT_BIND=0.0.0.0` and `MANUALMATE_AGENT_ALLOWED_HOSTS` to the Agent-A source IP on the Agent-B machine, start `run_agent_b.py`, then set `MANUALMATE_AGENT_HOST` and `MANUALMATE_AGENT_PORT` on the ManualMate machine. Keep the shared key private and use a trusted network. If the key variable is absent, a local-only key is created under `.manualmate/` and ignored by Git; that local key is not automatically shared with another machine.

## Demo-mode note

The built-in manual’s answers and troubleshooting guidance are sample, predefined demo responses. Uploaded PDFs are processed locally by default. If you explicitly opt into Gemini for a specific upload, ManualMate sends only the extracted text to Google to organize setup steps. If you enable the recall checkbox, it sends only the product/model query to CPSC. Uploaded PDF question matching and tool/parts inventory extraction remain local. Scanned/image-only PDFs may need OCR and may not return useful answers. The included sample PDF is provided for trying the upload path.

## Files

- `app.py` — the Streamlit app
- `sample_manual.pdf` — built-in sample manual for upload testing
- `requirements.txt` — Python packages
- `run.bat` — Windows setup and launch helper
