from __future__ import annotations

import re
import logging
import hashlib
import os
from io import BytesIO
from html import escape
from pathlib import Path

import streamlit as st
from manualmate.negotiation import negotiate
from manualmate.utility import rank_candidates
from manualmate.messaging import local_socket_demo
from manualmate.worker import BackgroundWorker
from manualmate.assistance import process_assistance_request
from manualmate.pdf_steps import extract_inventory, extract_setup_steps
from manualmate.gemini_steps import GeminiStepsError
from manualmate.agent_pipeline import PDFReaderAgent, SetupDisplayAgent
from manualmate.bdi import BDIAgent
from manualmate.knowledge import CPSC_INFO_URL, KnowledgeLookupError, extract_product_identifier, lookup_product_recalls
from manualmate.security import MessageAuthenticationError, get_agent_key

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(filename=LOG_DIR / "manualmate.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")

try:
    from pypdf import PdfReader
except ImportError:  # A friendly message is shown if dependencies were not installed.
    PdfReader = None

try:
    import pypdfium2 as pdfium
except ImportError:  # A friendly no-preview message is shown if dependencies are missing.
    pdfium = None

st.set_page_config(page_title="ManualMate | Guided repair", page_icon="🛠️", layout="wide")

SAMPLE_PAGES = [
    """NORTHSTAR HOME | COMPACT DISHWASHER\nQUICK-START & SERVICE MANUAL\nModel NDW-240 · Revision 2.1\n\nWELCOME\nThis manual covers safe installation and basic troubleshooting for the Northstar NDW-240 compact dishwasher. Read all safety notes before servicing. Disconnect power before removing panels.\n\nIN THE BOX\nDishwasher (1), inlet hose (1), drain hose (1), hose clip (1), adjustable leveling feet (4), quick-connect adapter (1).\n\nTOOLS YOU WILL NEED\nAdjustable wrench, Phillips screwdriver, spirit level, towel, and a bucket.\n\nSAFETY\nWARNING: Always unplug the dishwasher before inspection or repair.\nCAUTION: Water may remain in the sump after a cycle. Keep a towel and bucket nearby.\nDo not pull the appliance by its hoses. Do not operate with a damaged power cord.""",
    """INSTALLATION CHECK\n1. Place the unit on a level, stable floor near a grounded outlet and sink connection. Leave at least 5 cm (2 in) of clearance behind the unit.\n2. Connect the inlet hose to the cold-water tap. Hand-tighten, then use an adjustable wrench for no more than a quarter turn. Do not overtighten.\n3. Secure the drain hose to the sink edge with the supplied hose clip. The hose outlet must be 40–100 cm (16–39 in) above the floor.\n4. Adjust the four leveling feet until the spirit level reads even front-to-back and side-to-side.\n5. Check both hose connections for leaks before plugging in.\n\nFIRST TEST\nRun a short cycle with no dishes. Watch the inlet connection during fill and check beneath the unit after the cycle.""",
    """BASIC OPERATION\n1. Open the door and slide out the lower rack.\n2. Scrape large food scraps from dishes. Do not pre-rinse.\n3. Add one dishwasher tablet to the detergent cup and close the lid.\n4. Load dishes with soiled surfaces facing the spray arm. Keep the spray arm clear.\n5. Push the rack in, close the door, and press Power.\n6. Select Normal or Quick, then press Start. The cycle indicator flashes while the cycle is running.\n\nCARE\nClean the filter weekly: unplug the unit, turn the filter counter-clockwise, lift it out, rinse under running water, and reinstall clockwise until it stops. Wipe the door seal with a damp cloth.\n\nREFERENCE\nFilter: lower center of wash chamber. Inlet connection: rear upper-left. Drain connection: rear lower-right.""",
    """TROUBLESHOOTING\nUNIT DOES NOT START\nConfirm the plug is in a grounded outlet, the door is fully latched, and Start was pressed after selecting a cycle. If the outlet has no power, contact a qualified electrician.\n\nWATER REMAINS AFTER CYCLE\nCheck that the drain hose is not kinked and its outlet is between 40–100 cm above the floor. Clean the filter as described on page 3. Place a towel and bucket nearby before disconnecting a hose.\n\nLEAK AT INLET\nStop the cycle and unplug the unit. Close the water tap. Check that the inlet hose washer is seated and the connection is hand-tight plus no more than a quarter turn. Do not continue if leaking persists.\n\nPOOR CLEANING\nEnsure spray arm can rotate freely, dishes do not block detergent cup, and filter is clean.\n\nIf an electrical fault, damaged cord, or persistent leak is present, stop and contact Northstar service. Never remove the sealed pump housing.""",
]

PARTS = ["Dishwasher body", "Inlet hose", "Drain hose", "Hose clip", "Leveling feet (4)", "Quick-connect adapter"]
TOOLS = ["Adjustable wrench", "Phillips screwdriver", "Spirit level", "Towel", "Bucket"]
STEPS = [
    {"title": "Prepare the workspace", "body": "Set the dishwasher on a stable floor near the sink and a grounded outlet. Leave 5 cm (2 in) behind it.", "tip": "Keep the unit upright and leave the hoses loose until the connections are ready.", "page": 2},
    {"title": "Connect the water inlet", "body": "Attach the inlet hose to the cold-water tap. Hand-tighten, then use the wrench for no more than a quarter turn.", "tip": "Check that the hose washer is seated before tightening. Overtightening can damage the connection.", "page": 2},
    {"title": "Route the drain hose", "body": "Secure the drain hose at the sink edge with the supplied clip. Keep the outlet 40–100 cm (16–39 in) above the floor.", "tip": "Avoid kinks and sharp bends that can slow draining.", "page": 2},
    {"title": "Level the dishwasher", "body": "Adjust the four feet until your spirit level reads even from front-to-back and side-to-side.", "tip": "A level unit helps the door seal and the cycle drain correctly.", "page": 2},
    {"title": "Inspect, then run a test", "body": "Check both hose connections for leaks before plugging in. Run a short cycle with no dishes and watch the inlet during fill.", "tip": "Keep a towel and bucket nearby. If you see a leak, stop and unplug the unit.", "page": 2},
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
.stApp { background: #f5f7f5; color: #18231f; font-family: 'DM Sans', sans-serif; }
.stApp, [data-testid="stAppViewContainer"] { color:#18231f !important; color-scheme:light; }
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stMarkdownContainer"] p,
.stApp [data-testid="stMarkdownContainer"] li,
.stApp [data-testid="stWidgetLabel"],
.stApp [data-testid="stWidgetLabel"] *,
.stApp label,
.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stCaptionContainer"] *,
.stApp [data-testid="stTable"],
.stApp [data-testid="stDataFrame"],
.stApp [data-testid="stAlert"] { color:#26372f !important; }
.stApp [data-testid="stTabs"] button[role="tab"],
.stApp [data-baseweb="tab"],
.stApp [data-baseweb="tab"] * { color:#34483d !important; }
.stApp [data-testid="stTabs"] button[role="tab"][aria-selected="true"],
.stApp [data-baseweb="tab-highlight"] { color:#174c35 !important; border-bottom-color:#28734e !important; }
.stApp [data-testid="stButton"] button,
.stApp [data-testid="stFormSubmitButton"] button { color:#173d2d !important; background:#e4eee7 !important; border:1px solid #91ac99 !important; }
.stApp [data-testid="stButton"] button:hover,
.stApp [data-testid="stFormSubmitButton"] button:hover { color:#102d21 !important; background:#d3e4d8 !important; border-color:#527b60 !important; }
.stApp [data-testid="stButton"] button[kind="primary"],
.stApp [data-testid="stFormSubmitButton"] button[kind="primary"] { color:#fff !important; background:#1d6846 !important; border-color:#1d6846 !important; }
.stApp input, .stApp textarea, .stApp [data-baseweb="select"] *,
.stApp [data-baseweb="input"] *, .stApp [data-baseweb="textarea"] * { color:#1d2b24 !important; }
.stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"] > div,
.stApp [data-baseweb="textarea"] > div { background:#fff !important; border-color:#879b8d !important; }
h1,h2,h3 { font-family: 'Manrope',sans-serif !important; letter-spacing:-.035em; }
.block-container { padding-top: 2rem; max-width: 1200px; }
[data-testid="stSidebar"] { background: #edf2ee; border-right:1px solid #e1e8e2; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] label * { color:#24372e !important; }
.hero { background:linear-gradient(115deg,#173d31,#23644c); border-radius:24px; padding:32px 36px; color:white; margin-bottom:22px; position:relative; overflow:hidden; }
.hero:after {content:'✳'; position:absolute;right:34px;top:-42px;font-size:200px;color:#ffffff12;}
.hero-kicker {text-transform:uppercase;letter-spacing:.16em;font-size:11px;font-weight:700;color:#b5dfc5;}
.hero h1 {font-size:35px;margin:8px 0 6px;color:white;}
.hero p {color:#d7e8dc;margin:0;font-size:15px;}
.eyebrow {font-size:11px;text-transform:uppercase;letter-spacing:.15em;color:#668174;font-weight:700;margin-bottom:8px;}
.card {background:white;border:1px solid #e6ece7;border-radius:18px;padding:20px 22px;height:100%;box-shadow:0 3px 14px #1e382509;}
.metric {font:800 27px Manrope,sans-serif;color:#1d543e;}.metric-label {font-size:13px;color:#52645a;margin-top:4px;}
.step-card {background:#fff;border:1px solid #e6ece7;border-radius:22px;padding:27px 30px;box-shadow:0 7px 22px #1e38250d;}
.step-count {color:#39745a;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;}
.step-card h2 {font-size:27px;margin:8px 0 12px;}.step-card p {font-size:16px;line-height:1.7;color:#53645b;}
.callout {background:#f1f7f2;border-left:3px solid #438361;padding:12px 15px;border-radius:0 10px 10px 0;color:#365848;font-size:14px;margin-top:18px;}
.warning {background:#fff6e9;border-left:3px solid #df9a3c;padding:12px 15px;border-radius:0 10px 10px 0;color:#755421;font-size:14px;margin:12px 0;}
.tag {display:inline-block;background:#eaf3ec;color:#316047;padding:6px 10px;border-radius:99px;font-size:12px;font-weight:600;margin:3px;}
.footnote {font-size:12px;color:#52645a;}
</style>
"""


def pages_from_pdf(upload) -> list[str]:
    if PdfReader is None:
        st.error("PDF reading needs pypdf. Install the included requirements, then restart ManualMate.")
        return []
    try:
        pages = []
        for page in PdfReader(upload).pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except (TypeError, ValueError):
                text = page.extract_text() or ""
            pages.append(text)
        return pages
    except Exception:
        st.error("Could not read this PDF. Check that it is a valid PDF and try again.")
        return []


@st.cache_data(ttl=3600, max_entries=24, show_spinner=False)
def match_step_images(pdf_bytes: bytes, steps: list[dict[str, object]]) -> dict[int, bytes]:
    """Assign each embedded page image to its closest matching setup step once."""
    if pdfium is None or not steps:
        return {}
    document = None
    try:
        document = pdfium.PdfDocument(pdf_bytes)
        by_page: dict[int, list[tuple[int, dict[str, object]]]] = {}
        for step_index, step in enumerate(steps):
            try:
                page_number = int(step["page"])
            except (KeyError, TypeError, ValueError):
                continue
            by_page.setdefault(page_number, []).append((step_index, step))

        result: dict[int, bytes] = {}
        text_type = pdfium.raw.FPDF_PAGEOBJ_TEXT
        image_type = pdfium.raw.FPDF_PAGEOBJ_IMAGE
        for page_number, page_steps in by_page.items():
            if page_number < 1 or page_number > len(document):
                continue
            page = document[page_number - 1]
            page_width, page_height = page.get_size()
            textpage = page.get_textpage()
            try:
                # Merge adjacent PDF text objects into lines so split words still match.
                lines: list[dict[str, object]] = []
                objects = []
                for text_obj in page.get_objects(filter=[text_type], textpage=textpage):
                    text = re.sub(r"\s+", " ", text_obj.extract()).strip()
                    bounds = text_obj.get_bounds()
                    if text and bounds:
                        objects.append((bounds, text))
                objects.sort(key=lambda item: (-((item[0][1] + item[0][3]) / 2), item[0][0]))
                for bounds, text in objects:
                    center_y = (bounds[1] + bounds[3]) / 2
                    line = next((
                        item for item in reversed(lines)
                        if abs(float(item["center_y"]) - center_y) < 4
                        and bounds[0] - float(item["bounds"][2]) < 24
                    ), None)
                    if line:
                        old = line["bounds"]
                        line["bounds"] = (min(old[0], bounds[0]), min(old[1], bounds[1]), max(old[2], bounds[2]), max(old[3], bounds[3]))
                        line["text"] = f'{line["text"]} {text}'
                        line["center_y"] = (line["bounds"][1] + line["bounds"][3]) / 2
                    else:
                        lines.append({"bounds": bounds, "text": text, "center_y": center_y})

                anchors: dict[int, tuple[float, float, float]] = {}
                for step_index, step in page_steps:
                    terms = set(re.findall(r"[a-z0-9]{3,}", f'{step.get("title", "")} {step.get("body", "")}'.lower()))
                    terms -= {"the", "and", "for", "with", "from", "then", "this", "that", "into", "until", "your", "you", "after", "before"}
                    best_line = None
                    best_score = 0.0
                    for line in lines:
                        line_terms = set(re.findall(r"[a-z0-9]{3,}", str(line["text"]).lower()))
                        shared = terms & line_terms
                        score = len(shared) / max(1, len(terms))
                        if len(shared) >= 2 and score > best_score:
                            best_line, best_score = line, score
                    if best_line:
                        x0, y0, x1, y1 = best_line["bounds"]
                        anchors[step_index] = ((x0 + x1) / 2, (y0 + y1) / 2, best_score)

                image_items: list[tuple[float, float, float, bytes]] = []
                seen_images: set[str] = set()
                for image_obj in page.get_objects(filter=[image_type]):
                    try:
                        x0, y0, x1, y1 = image_obj.get_bounds()
                        box_width, box_height = abs(x1 - x0), abs(y1 - y0)
                        area_ratio = (box_width * box_height) / max(1, page_width * page_height)
                        if area_ratio < 0.025 or area_ratio > 0.75:
                            continue
                        bitmap = image_obj.get_bitmap()
                        try:
                            image = bitmap.to_pil().convert("RGB")
                            if image.width < 60 or image.height < 45:
                                image.close()
                                continue
                            output = BytesIO()
                            image.save(output, format="PNG", optimize=True)
                            image.close()
                            image_data = output.getvalue()
                            image_digest = hashlib.sha256(image_data).hexdigest()
                            if image_digest not in seen_images:
                                image_items.append(((x0 + x1) / 2, (y0 + y1) / 2, area_ratio, image_data))
                                seen_images.add(image_digest)
                        finally:
                            bitmap.close()
                    except Exception:
                        logging.getLogger("manualmate.pdf").exception("Could not read a page image")

                # Greedily pair the closest image/text anchors, never reusing an image.
                pairings = []
                for step_index, (step_x, step_y, text_score) in anchors.items():
                    for image_index, (image_x, image_y, _area, _data) in enumerate(image_items):
                        distance = (((step_x - image_x) / max(1, page_width)) ** 2 + ((step_y - image_y) / max(1, page_height)) ** 2) ** 0.5
                        pairings.append((distance, -text_score, step_index, image_index))
                used_steps: set[int] = set()
                used_images: set[int] = set()
                for distance, _score, step_index, image_index in sorted(pairings):
                    if distance > 0.65:
                        continue
                    if step_index in used_steps or image_index in used_images:
                        continue
                    result[step_index] = image_items[image_index][3]
                    used_steps.add(step_index)
                    used_images.add(image_index)
            finally:
                textpage.close()
                page.close()
        return result
    except Exception:
        logging.getLogger("manualmate.pdf").exception("Could not match PDF images to setup steps")
        return {}
    finally:
        if document is not None:
            document.close()


def get_gemini_key() -> str:
    environment_key = os.environ.get("GEMINI_API_KEY", "")
    if environment_key:
        return environment_key
    try:
        return str(st.secrets.get("GEMINI_API_KEY", ""))
    except (FileNotFoundError, KeyError):
        return ""


@st.cache_data(ttl=1800, show_spinner=False)
def cached_recall_lookup(product_name: str) -> dict:
    return lookup_product_recalls(product_name)


def answer_for(question: str, current: int, pages: list[str]) -> tuple[str, int]:
    q = question.lower()
    # Uploaded manuals always use their extracted text and actual page numbers.
    if pages != SAMPLE_PAGES:
        terms = [w for w in re.findall(r"[a-z]{4,}", q) if w not in {"what", "when", "where", "which", "should", "does", "with", "from", "this", "that", "manual", "please"}]
        expanded = terms + (["above", "height", "outlet"] if "high" in terms else []) + (["below"] if "low" in terms else [])
        ranked = sorted(enumerate(pages, 1), key=lambda pair: sum(pair[1].lower().count(term) for term in expanded), reverse=True)
        if ranked and expanded and sum(ranked[0][1].lower().count(term) for term in expanded):
            page_no, text = ranked[0]
            sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
            matches = [s.strip() for s in sentences if any(term in s.lower() for term in expanded)]
            matches.sort(key=lambda s: (sum(s.lower().count(term) for term in expanded), sum(s.lower().count(term) for term in terms)), reverse=True)
            excerpt = " ".join(matches[:3]) or text[:450]
            return f"I found this relevant passage in your uploaded manual: {excerpt}", page_no
        return "I couldn’t find a clear match in the text extracted from this PDF. Try a more specific phrase, or check the page list in Overview.", 1

    # The sample responses are intentionally fixed and visibly labeled as demo content.
    topics = [
        (("leak", "water", "inlet", "hose"), "Stop the cycle and unplug the dishwasher. Close the water tap, then check that the inlet hose washer is seated and the connection is hand-tight plus no more than a quarter turn. If leaking persists, stop and contact service.", 4),
        (("drain", "water remains", "standing water", "filter"), "Check for a kink in the drain hose, confirm its outlet is 40–100 cm above the floor, and clean the filter. Keep a towel and bucket nearby before disconnecting a hose.", 4),
        (("tool", "wrench", "need"), "For the installation, gather an adjustable wrench, Phillips screwdriver, spirit level, towel, and bucket.", 1),
        (("part", "box", "included"), "The box includes the dishwasher, inlet hose, drain hose, hose clip, four leveling feet, and quick-connect adapter.", 1),
        (("start", "power", "doesn't start", "not start"), "Confirm it is plugged into a grounded outlet, the door is fully latched, and Start was pressed after selecting a cycle. If the outlet has no power, contact a qualified electrician.", 4),
        (("level", "clearance", "height", "above"), "Leave 5 cm (2 in) behind the unit. Set the drain hose outlet 40–100 cm (16–39 in) above the floor.", 2),
        (("filter", "clean"), "Unplug the unit, turn the filter counter-clockwise, lift it out, rinse it, then reinstall clockwise until it stops.", 3),
        (("spray", "dishes", "load"), "Load dishes with soiled surfaces facing the spray arm and make sure the arm can rotate freely. Keep the detergent cup clear.", 3),
    ]
    for triggers, response, page in topics:
        if any(word in q for word in triggers):
            return response, page
    # Ground the fallback in the current procedure step and cite that source page.
    step = STEPS[current]
    return f"For this step, {step['body'][0].lower() + step['body'][1:]} {step['tip']}", step["page"]


@st.cache_resource
def shared_agent_worker() -> BackgroundWorker:
    worker = BackgroundWorker(interval=10.0)
    worker.start()
    return worker


@st.fragment(run_every="500ms")
def poll_background_help() -> None:
    pending = st.session_state.get("pending_help")
    if not pending:
        return
    future = pending["future"]
    if not future.done():
        st.info("ManualMate’s background agents are checking your question…")
        return
    try:
        result = future.result()
        st.session_state.chat.append((pending["question"], result))
        st.session_state.last_assistance_result = result
    except Exception as exc:
        st.error(f"The background help request failed: {exc}")
    st.session_state.pending_help = None
    st.rerun(scope="app")


def main():
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("## 🛠️ ManualMate")
        st.caption("Your manual, made easier to follow")
        st.markdown("---")
        source = st.radio("Choose a manual", ["Sample dishwasher manual", "Upload a PDF"], label_visibility="visible")
        uploaded = None
        use_gemini = False
        recall_check_enabled = st.checkbox(
            "Check official product recall notices",
            value=False,
            key="official_recall_check",
        )
        recall_product_override = ""
        if recall_check_enabled:
            recall_product_override = st.text_input(
                "Product/model name (optional)",
                help="ManualMate sends only this product/model name to the public CPSC recall service; the PDF remains local.",
            )
            st.caption("The recall check is optional. Verify any possible match against the official notice.")
        extractor = "Local parser"
        if source == "Upload a PDF":
            uploaded = st.file_uploader("Choose a PDF manual", type=["pdf"], help="Text-based PDFs work best.")
            if uploaded:
                uploaded_key = hashlib.sha256(uploaded.getvalue()).hexdigest()
                if st.session_state.get("remote_consent_identity") != uploaded_key:
                    st.session_state.gemini_upload_consent = False
                    st.session_state.remote_consent_identity = uploaded_key
                extractor = st.selectbox("Step extraction", ["Local parser", "Gemini"], key="pdf_extractor_provider")
                if extractor == "Gemini":
                    use_gemini = st.checkbox("Send extracted text to Gemini for this PDF", key="gemini_upload_consent")
                    st.caption("If checked, only extracted text is sent to Google Gemini. The PDF itself is not uploaded. Verify each generated step and citation.")
                else:
                    st.caption("Processes the PDF locally; no text is sent to an AI provider.")
        else:
            st.session_state.remote_consent_identity = None
            st.session_state.gemini_upload_consent = False
        st.markdown("---")
        st.markdown("**Demo mode**")
        if source == "Sample dishwasher manual":
            st.caption("The built-in manual uses sample, predefined guidance. It makes no external AI calls.")
        else:
            st.caption("Uploaded manuals use the local parser unless you choose a provider and explicitly consent for that PDF.")

    is_uploaded_manual = source == "Upload a PDF" and uploaded is not None
    if is_uploaded_manual:
        pages = pages_from_pdf(uploaded)
        manual_name = uploaded.name
        manual_key = f"{manual_name}:{hashlib.sha256(uploaded.getvalue()).hexdigest()}"
        guide_steps = extract_setup_steps(pages)
        inventory = extract_inventory(pages)
        steps_source = "Local text parser"
        if use_gemini:
            api_key = get_gemini_key()
            if not api_key:
                st.warning("Gemini is enabled for this PDF, but no key is configured. Rotate the key pasted into chat, then add GEMINI_API_KEY to .streamlit/secrets.toml. ManualMate will keep using its local parser for now.")
            else:
                cached_steps = st.session_state.setdefault("gemini_steps_cache", {})
                if manual_key in cached_steps:
                    guide_steps = cached_steps[manual_key]
                    steps_source = "Gemini"
                else:
                    try:
                        with st.spinner("Gemini is organizing this manual’s setup steps…"):
                            reader_agent = PDFReaderAgent(get_agent_key())
                            signed_handoff = reader_agent.extract_and_sign(pages, api_key)
                            display_agent = SetupDisplayAgent(get_agent_key())
                            guide_steps = display_agent.verify_for_display(signed_handoff, pages)
                        cached_steps[manual_key] = guide_steps
                        steps_source = "Gemini"
                    except MessageAuthenticationError as exc:
                        logging.getLogger("manualmate.security").warning("setup display agent rejected Gemini extraction: %s", exc)
                        st.error(f"The setup display agent rejected the Gemini result: {exc} Local parser results are shown instead.")
                    except GeminiStepsError as exc:
                        st.warning(f"Gemini step extraction failed: {exc} The local parser results are shown instead.")
    else:
        pages = SAMPLE_PAGES
        manual_name = "Northstar NDW-240 · Quick-start & service manual"
        manual_key = "builtin-northstar-ndw-240"
        guide_steps = STEPS
        steps_source = "Built-in sample"
        inventory = None

    product_identifier = recall_product_override.strip() or extract_product_identifier(pages, manual_name)
    recall_knowledge = {"status": "not_checked", "product_query": product_identifier, "matches": [], "source": CPSC_INFO_URL}
    if recall_check_enabled:
        if product_identifier:
            try:
                with st.spinner(f"Checking official recall notices for {product_identifier}..."):
                    recall_knowledge = cached_recall_lookup(product_identifier)
            except KnowledgeLookupError as exc:
                recall_knowledge = {"status": "unavailable", "product_query": product_identifier, "matches": [], "source": CPSC_INFO_URL, "error": str(exc)}
        else:
            recall_knowledge["status"] = "no_product_identifier"

    if not pages:
        st.info("Upload a text-based PDF or switch to the built-in sample manual.")
        return

    if "step" not in st.session_state:
        st.session_state.step = 0
    if "done" not in st.session_state:
        st.session_state.done = False
    if "chat" not in st.session_state:
        st.session_state.chat = []
    if st.session_state.get("active_manual") != manual_key:
        st.session_state.active_manual = manual_key
        st.session_state.step = 0
        st.session_state.done = False
        st.session_state.chat = []
        st.session_state.show_stuck = False

    st.markdown(f"""<div class="hero"><div class="hero-kicker">A calmer way to get it done</div><h1>Let’s set up your dishwasher.</h1><p>{escape(manual_name)} &nbsp;·&nbsp; Guided setup &nbsp;·&nbsp; Manual-grounded help</p></div>""", unsafe_allow_html=True)
    tab_overview, tab_guide, tab_ask, tab_agents = st.tabs(["01  Overview", "02  Guided setup", "03  Ask the manual", "04  Agent lab"])

    with tab_overview:
        st.markdown('<div class="eyebrow">Before you begin</div>', unsafe_allow_html=True)
        if recall_knowledge["status"] == "checked":
            if recall_knowledge["matches"]:
                st.warning(f"The public recall search found {len(recall_knowledge['matches'])} possible result(s) for {product_identifier}. Confirm the exact model in the official notice before repair.")
                for match in recall_knowledge["matches"][:3]:
                    st.markdown(f"- **{escape(match.get('product') or 'Product recall')}** · {escape(match.get('hazard') or 'Review official notice')} · {escape(match.get('date') or '')}")
            else:
                st.success(f"No matching CPSC recall listing was returned for {product_identifier}. This search is not a guarantee that no safety issue exists.")
            st.caption(f"Live source: {CPSC_INFO_URL}")
        elif recall_knowledge["status"] == "unavailable":
            st.info(f"The official recall lookup is unavailable right now. The manual guidance remains available. {recall_knowledge.get('error', '')}")
        elif recall_check_enabled:
            st.info("No product/model name was found. Enter one in the sidebar to check official recall notices.")
        if is_uploaded_manual:
            st.caption("Quick scan of text extracted from your uploaded manual. Review the cited pages before acting; scanned PDFs may need OCR.")
            parts_found = inventory["parts"]
            tools_found = inventory["tools"]
            warnings_found = []
            for page_no, page_text in enumerate(pages, 1):
                for line in page_text.splitlines():
                    clean = line.strip()
                    if clean and re.search(r"\b(warning|caution|danger|unplug|disconnect power)\b", clean, re.I):
                        warnings_found.append((page_no, clean))
            warnings_found = warnings_found[:8]
            left,right=st.columns(2)
            for col,title,items,empty in [(left,"Parts & included items",parts_found,"No explicit parts list found in extracted text."),(right,"Tools & equipment",tools_found,"No explicit tools list found in extracted text.")]:
                with col:
                    st.markdown(f'<div class="card"><div class="eyebrow">{title}</div>'+''.join(f'<span class="tag">{escape(str(item["name"]))} <span class="footnote">· p. {int(item["page"])}</span></span>' for item in items) + (f'<p class="footnote">{empty}</p>' if not items else '')+'</div>',unsafe_allow_html=True)
            if warnings_found:
                for page,line in warnings_found[:4]: st.markdown(f'<div class="warning"><b>Manual safety note · p. {page}</b><br>{escape(line)}</div>',unsafe_allow_html=True)
            else:
                st.info("No obvious safety keywords were found in the PDF text. Review the manual’s safety section before proceeding.")
            st.markdown("**Extracted manual pages**")
            for i,p in enumerate(pages,1):
                with st.expander(f"Page {i}"):
                    st.write(p or "No selectable text found on this page.")
        else:
            st.caption("Northstar NDW-240 sample manual")
            a,b,c=st.columns(3)
            for col,number,label in [(a,"6","Parts in the box"),(b,"5","Tools to gather"),(c,"2","Safety reminders")]:
                with col: st.markdown(f'<div class="card"><div class="metric">{number}</div><div class="metric-label">{label}</div></div>',unsafe_allow_html=True)
            st.write("")
            left,right=st.columns([1,1])
            with left:
                st.markdown('<div class="card"><div class="eyebrow">Parts included</div>'+''.join(f'<span class="tag">{x}</span>' for x in PARTS)+'</div>',unsafe_allow_html=True)
            with right:
                st.markdown('<div class="card"><div class="eyebrow">Tools to gather</div>'+''.join(f'<span class="tag">{x}</span>' for x in TOOLS)+'</div>',unsafe_allow_html=True)
            st.markdown('<div class="warning"><b>Safety first.</b> Unplug before inspection or repair. Water may remain in the sump; keep a towel and bucket nearby.</div>',unsafe_allow_html=True)
            st.markdown("**Manual contents**")
            for i,p in enumerate(pages,1):
                snippets = [line.strip() for line in p.splitlines() if line.strip()]
                title = next((x for x in snippets if x.isupper() and len(x)<65),f"Page {i}")
                with st.expander(f"Page {i} · {title}"):
                    st.write(p)

    with tab_guide:
        current_result = st.session_state.get("last_assistance_result") or {}
        guided_agent = BDIAgent({
            "manual_name": manual_name,
            "current_step": min(st.session_state.step + 1, max(1, len(guide_steps))),
            "setup_complete": st.session_state.done,
            "recall_status": recall_knowledge["status"],
            "recall_matches": recall_knowledge.get("matches", []),
            "safety_category": current_result.get("triage", {}).get("category", ""),
        })
        guided_agent.set_desire("complete_setup_safely", priority=50)
        with st.expander("ManualMate live plan", expanded=False):
            live_state = current_result.get("agent_state", guided_agent.snapshot())
            state_cols = st.columns(3)
            state_cols[0].markdown("**Current beliefs**")
            state_cols[0].write({"manual": manual_name, "step": f"{min(st.session_state.step + 1, max(1, len(guide_steps)))}/{max(1, len(guide_steps))}", "recall check": recall_knowledge["status"], "safety": live_state.get("beliefs", {}).get("safety_category", "clear")})
            state_cols[1].markdown("**Active goal**")
            state_cols[1].write((live_state.get("desires") or guided_agent.desires)[0]["name"])
            state_cols[2].markdown("**Next actions**")
            state_cols[2].write(live_state.get("intentions") or guided_agent.intentions)
            st.caption("ManualMate updates this plan from the current step, recall lookup, and safety-agent response.")
        if is_uploaded_manual and not guide_steps:
            st.warning("I could read the PDF pages, but couldn’t find clear setup instructions to turn into steps. This can happen with scanned PDFs or manuals that use unusual layouts. Try a text-based PDF or check the extracted pages in Overview.")
        elif st.session_state.done:
            total=len(guide_steps)
            st.success("You’ve reached the end of the steps found in this manual.")
            st.progress(1.0, text=f"{total} of {total} steps complete")
            if st.button("Review the steps again"):
                st.session_state.step=0; st.session_state.done=False; st.rerun()
        elif guide_steps:
            total=len(guide_steps)
            if is_uploaded_manual:
                pages_found = sorted({int(step["page"]) for step in guide_steps})
                st.success(f"Found {total} guided steps on PDF pages {', '.join(map(str, pages_found))} · source: {steps_source}.")
                if steps_source == "Gemini":
                    st.caption("Gemini may summarize or misread instructions. Check the exact wording and safety notes on each cited page before acting.")
            if st.session_state.step >= total:
                st.session_state.step = 0
            idx=min(st.session_state.step,total-1); step=guide_steps[idx]
            st.progress(idx/total, text=f"Step {idx+1} of {total} · {int(idx/total*100)}% complete")
            tip_label = "Safety note from the manual" if is_uploaded_manual else "Helpful tip"
            if is_uploaded_manual:
                matched_images = match_step_images(uploaded.getvalue(), guide_steps)
                step_col, image_col = st.columns([1, 1], gap="large")
                with step_col:
                    st.markdown(f'<div class="step-card"><div class="step-count">STEP {idx+1:02d} &nbsp; / &nbsp; {total:02d} · MANUAL PAGE {step["page"]}</div><h2>{escape(str(step["title"]))}</h2><p>{escape(str(step["body"]))}</p><div class="callout"><b>{tip_label}</b> &nbsp; {escape(str(step["tip"]))}</div></div>',unsafe_allow_html=True)
                with image_col:
                    source_page = int(step["page"])
                    step_image = matched_images.get(idx)
                    st.markdown(f"**Diagram for this step · Page {source_page}**")
                    if step_image:
                        st.image(step_image, caption=f"Matched to step {idx+1} · page {source_page}", use_container_width=True)
                    else:
                        st.info(f"No distinct diagram or photo was matched to this step on PDF page {source_page}.")
            else:
                st.markdown(f'<div class="step-card"><div class="step-count">STEP {idx+1:02d} &nbsp; / &nbsp; {total:02d} · MANUAL PAGE {step["page"]}</div><h2>{escape(str(step["title"]))}</h2><p>{escape(str(step["body"]))}</p><div class="callout"><b>{tip_label}</b> &nbsp; {escape(str(step["tip"]))}</div></div>',unsafe_allow_html=True)
            st.write("")
            back,nextcol,stuck=st.columns([1,1,1])
            with back:
                if st.button("← Back",disabled=idx==0,use_container_width=True): st.session_state.step-=1; st.rerun()
            with nextcol:
                label="Finish setup ✓" if idx==total-1 else "Next step →"
                if st.button(label,type="primary",use_container_width=True):
                    if idx==total-1: st.session_state.done=True
                    else: st.session_state.step+=1
                    st.rerun()
            with stuck:
                if st.button("🆘  I’m stuck",use_container_width=True): st.session_state.show_stuck=not st.session_state.get("show_stuck",False)
            if st.session_state.get("show_stuck",False):
                st.markdown("#### Let’s troubleshoot this step")
                st.caption(f"We’ll focus on: **{step['title']}**")
                issue=st.selectbox("What’s getting in the way?",["I’m not sure how to do this", "A connection is leaking", "The hose won’t reach", "The unit doesn’t seem level", "Something else"])
                if st.button("Show me what to check"):
                    if "leak" in issue.lower(): response="Stop and unplug the unit, then close the tap. Reseat the washer and tighten by hand plus no more than a quarter turn. Put the towel and bucket under the connection; stop if it still leaks."
                    elif "reach" in issue.lower(): response="Check the route for kinks and confirm the drain outlet can sit 40–100 cm above the floor. Don’t stretch or pull the appliance by its hoses."
                    elif "level" in issue.lower(): response="Place the spirit level on a flat surface inside the unit, then adjust the feet a little at a time until the bubble is centered front-to-back and side-to-side."
                    elif is_uploaded_manual: response=f"Pause and review the instruction on page {step['page']}: {step['body']} If you’re still unsure or the task seems unsafe, contact the appliance maker or a qualified technician."
                    else: response=f"Try this for **{step['title']}**: {step['body']} Keep in mind: {step['tip']}"
                    st.info(response); st.caption(f"From your manual · page {step['page']}" + (" · Sample guidance" if not is_uploaded_manual else ""))

    with tab_ask:
        st.markdown("### Ask a question about your manual")
        st.caption("ManualMate answers from the selected manual, then its background agent checks safety, sends the active request to Agent B, and ranks relevant assistance. Try “The inlet is leaking.”")
        with st.form("ask_form",clear_on_submit=True):
            question=st.text_input("Your question",placeholder="e.g. How high should the drain hose be?")
            submitted=st.form_submit_button("Ask ManualMate",type="primary")
        if submitted and question.strip():
            worker = shared_agent_worker()
            worker.start()
            current_step = min(st.session_state.step, max(0, len(guide_steps)-1))
            setup_done = st.session_state.done
            st.session_state.last_question = question
            st.session_state.pending_help = {
                "question": question,
                "future": worker.submit(
                    "manual-help-request",
                    lambda: process_assistance_request(
                        question,
                        current_step,
                        manual_name,
                        setup_done,
                        lambda prompt: answer_for(prompt, current_step, pages),
                    recall_knowledge=recall_knowledge,
                    ),
                ),
            }
        poll_background_help()
        for item in reversed(st.session_state.chat):
            if len(item) == 2:
                q, result = item
                a, p = result["answer"], result["page"]
            else:  # retain questions from a Streamlit session created before this integration
                q, a, p = item
                result = None
            st.markdown(f"**You asked:** {q}")
            st.info(a)
            st.caption(f"Manual reference · page {p} · Sample response")
            if result:
                choice = result["selected"]
                if choice is not None:
                    st.success(f"Agents recommend: {choice.name} · utility {choice.utility:.1f} · estimated price ${choice.price:.0f}")
                else:
                    st.warning("ManualMate paused repair recommendations until the possible recall is verified.")
                st.caption(f"Authenticated Agent B classified the request as: {result['triage']['category']}")
                with st.expander("View agent negotiation timeline"):
                    for line in result["negotiation"]:
                        st.write(line)
                with st.expander("View ManualMate's live decision state"):
                    st.write("Beliefs", result["agent_state"]["beliefs"])
                    st.write("Desire", result["agent_state"]["desires"])
                    st.write("Current intention", result["agent_state"]["intentions"])
                    st.write("Message security", result["security"])

    with tab_agents:
        st.markdown("### Assignment 2.1 · Background worker")
        worker = shared_agent_worker()
        a, b = st.columns(2)
        if a.button("Start monitor"):
            worker.start()
        if b.button("Queue manual scan"):
            worker.submit("manual-page-scan")
        if st.button("Stop monitor"):
            worker.stop()
        st.caption("A daemon thread polls periodically, processes queued work, and protects shared metrics with a lock. Timings are written to logs/manualmate.log.")
        metrics = worker.snapshot()
        if metrics:
            st.dataframe(metrics[-8:], width="stretch")

        st.markdown("### Assignment 2.2 · Local TCP messaging")
        if st.button("Send active state from Agent A to Agent B", type="primary"):
            try:
                incoming, ack = local_socket_demo({"step": min(st.session_state.step + 1, len(guide_steps)), "manual": manual_name, "setup_complete": st.session_state.done}, signing_key=get_agent_key())
                st.success(f"Agent B parsed {incoming['type']} from {incoming['sender']} and replied: {ack['payload']['summary']}")
                st.json({"received": incoming, "response": ack})
            except (OSError, ValueError, KeyError) as exc:
                st.error(f"Socket demo failed: {exc}")

        st.markdown("### Assignment 3.1 · Contract-net negotiation")
        if st.button("Run negotiation"):
            for line in negotiate():
                st.code(line, language="text")

        st.markdown("### Assignment 3.2 · Assistance utility optimization")
        st.markdown("**U = 5 × relevance + 1.5 × minutes saved + 3 × safety − 2 × price.** Relevance and safety are scored 1–10. The buyer requires relevance ≥ 6 and safety ≥ 7; the negotiated price must stay within the $30 budget. ManualMate selects the valid agreement with the highest utility after negotiation.")
        previous_result = st.session_state.get("last_assistance_result")
        if previous_result:
            ranked, selected = previous_result["ranked"], previous_result["selected"]
            st.caption("These offers include negotiated prices. ManualMate recommends the highest-utility valid agreement.")
        else:
            ranked, selected = rank_candidates(st.session_state.get("last_question", ""))
        if st.session_state.get("last_question"):
            st.caption(f"Scores are adjusted for your latest question: {st.session_state.last_question}")
        st.dataframe([{"Candidate": item.name, "Relevance": item.relevance, "Minutes saved": item.time_saved_min, "Negotiated price ($)": item.price, "Safety": item.safety, "Utility after negotiation": round(item.utility, 1), "Valid agreement": valid} for item, valid in ranked], width="stretch")
        st.success(f"Selected: {selected.name} · utility {selected.utility:.1f}")

    st.markdown('<p class="footnote" style="text-align:center;margin-top:34px">ManualMate college demo · Responses are predefined samples · Always follow your appliance maker’s safety guidance</p>',unsafe_allow_html=True)


if __name__ == "__main__":
    main()
