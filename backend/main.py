import base64
import json
import os
from pathlib import Path
from typing import List, Literal, Optional, Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError


load_dotenv()

app = FastAPI(title="AI Product Listing Generator MVP (OpenAI)")

BASE_DIR = Path(__file__).resolve().parent

candidates = [
    BASE_DIR / "frontend",
    BASE_DIR.parent / "frontend",
    BASE_DIR.parent.parent / "frontend",
]

FRONTEND_DIR = None
for p in candidates:
    if (p / "index.html").exists():
        FRONTEND_DIR = p
        break

if FRONTEND_DIR is None:
    raise RuntimeError(
        "frontend/index.html not found. Expected one of:\n" + "\n".join([str(c) for c in candidates])
    )

INDEX_FILE = FRONTEND_DIR / "index.html"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is missing. Create .env and set OPENAI_API_KEY=...")

client = OpenAI(api_key=api_key)

MODEL_ID = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "8"))

Marketplace = Literal["olx", "wildberries", "ozon"]
Lang = Literal["ru", "kz", "en"]


class UniversalProduct(BaseModel):
    product_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    condition: Optional[str] = None
    key_attributes: List[str] = Field(default_factory=list)
    detected_text: List[str] = Field(default_factory=list)
    uncertainty: List[str] = Field(default_factory=list)


class ListingVariant(BaseModel):
    title: str
    bullets: List[str]
    description: str
    keywords: List[str]
    attributes: Dict[str, Any] = Field(default_factory=dict)
    compliance_todos: List[str] = Field(default_factory=list)
    uncertainty: List[str] = Field(default_factory=list)


class MarketplacePack(BaseModel):
    olx: ListingVariant
    wildberries: ListingVariant
    ozon: ListingVariant


class ListingBundle(BaseModel):
    lang: Lang
    universal: UniversalProduct
    listings: MarketplacePack


def to_data_url(file_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(file_bytes).decode("utf-8")
    safe_mime = mime if mime else "image/jpeg"
    return f"data:{safe_mime};base64,{b64}"


def extract_response_text(resp) -> str:
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str) and resp.output_text.strip():
        return resp.output_text

    out = []
    try:
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", None) == "message":
                for c in getattr(item, "content", []) or []:
                    ctype = getattr(c, "type", None)
                    if ctype in ("output_text", "text"):
                        out.append(getattr(c, "text", "") or "")
    except Exception:
        pass

    text = "".join(out).strip()
    if not text:
        raise RuntimeError("Failed to extract text from model response")
    return text


def build_prompt(lang: str, hint: str) -> str:
    return f"""
You generate product listings from photos for Kazakhstan/CIS marketplaces.

Requirements:
1) First build "universal" using ONLY what is visible in the photos.
2) Then generate 3 marketplace variants: olx, wildberries, ozon.
3) Do NOT invent facts. If uncertain, put it into "uncertainty" (and/or "compliance_todos").
4) Output language must be: {lang}.
5) If the user hint contradicts the photos, mention that in "uncertainty".

User hint (may be empty):
{hint or ""}

Return ONLY a valid JSON object that matches EXACTLY this structure:

{{
  "lang": "ru|kz|en",
  "universal": {{
    "product_type": "string",
    "brand": "string|null",
    "model": "string|null",
    "color": "string|null",
    "material": "string|null",
    "condition": "string|null",
    "key_attributes": ["..."],
    "detected_text": ["..."],
    "uncertainty": ["..."]
  }},
  "listings": {{
    "olx": {{
      "title": "string",
      "bullets": ["..."],
      "description": "string",
      "keywords": ["..."],
      "attributes": {{"key":"value"}},
      "compliance_todos": ["..."],
      "uncertainty": ["..."]
    }},
    "wildberries": {{ "...same fields..." }},
    "ozon": {{ "...same fields..." }}
  }}
}}

All fields must be present, even if lists are empty.
""".strip()


@app.get("/", response_class=HTMLResponse)
def home():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/olx", response_class=HTMLResponse)
def page_olx():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/wb", response_class=HTMLResponse)
def page_wb():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/ozon", response_class=HTMLResponse)
def page_ozon():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.post("/api/generate", response_model=ListingBundle)
async def generate(
    lang: Lang = Form(...),
    files: List[UploadFile] = File(...),
    hint: Optional[str] = Form(None),
):
    if not files:
        raise HTTPException(status_code=400, detail="No images provided")

    if len(files) > MAX_IMAGES:
        files = files[:MAX_IMAGES]

    content_parts = []
    prompt = build_prompt(lang=lang, hint=hint or "")
    content_parts.append({"type": "input_text", "text": prompt})

    for f in files:
        data = await f.read()
        if not data:
            continue
        content_parts.append({
            "type": "input_image",
            "image_url": to_data_url(data, mime=f.content_type or "image/jpeg")
        })

    if len(content_parts) < 2:
        raise HTTPException(status_code=400, detail="Images are empty or unsupported")

    try:
        resp = client.responses.create(
            model=MODEL_ID,
            input=[{"role": "user", "content": content_parts}],
            text={"format": {"type": "json_object"}},
            store=False,
        )
        text = extract_response_text(resp)
        data = json.loads(text)
        bundle = ListingBundle.model_validate(data)
        return bundle

    except (json.JSONDecodeError, ValidationError) as e:
        try:
            fix_prompt = f"""
Your JSON failed validation. Fix the JSON to match ListingBundle exactly.
Return ONLY valid JSON.

Validation error:
{str(e)}

Previous JSON:
{text}
""".strip()

            fix_resp = client.responses.create(
                model=MODEL_ID,
                input=[{"role": "user", "content": [{"type": "input_text", "text": fix_prompt}]}],
                text={"format": {"type": "json_object"}},
                store=False,
            )
            fix_text = extract_response_text(fix_resp)
            fix_data = json.loads(fix_text)
            bundle = ListingBundle.model_validate(fix_data)
            return bundle
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Model output validation failed: {str(e2)}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})
