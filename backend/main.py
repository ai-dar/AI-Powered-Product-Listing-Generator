import base64
import json
import os
import time
from pathlib import Path
from typing import List, Literal, Optional, Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from psycopg2.errors import UniqueViolation

from auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserResponse,
    hash_password, verify_password, create_access_token, get_current_user, get_optional_user
)
from database import (
    create_user, get_user_by_email,
    save_generation, get_user_history, get_generation_by_id, delete_generation
)


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

MODEL_ID = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
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


# --- Auth Routes ---

@app.get("/login", response_class=HTMLResponse)
def page_login():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/register", response_class=HTMLResponse)
def page_register():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard():
    return INDEX_FILE.read_text(encoding="utf-8")


@app.post("/api/auth/register", response_model=TokenResponse)
async def register(data: RegisterRequest):
    """Register a new user account."""
    try:
        password_hash = hash_password(data.password)
        user = create_user(
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name
        )
        token, expires_in = create_access_token(user["id"])
        return TokenResponse(access_token=token, expires_in=expires_in)
    except UniqueViolation:
        raise HTTPException(status_code=400, detail="Email already registered")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(data: LoginRequest):
    """Login with email and password."""
    user = get_user_by_email(data.email)

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=401, detail="Account is disabled")

    token, expires_in = create_access_token(user["id"])
    return TokenResponse(access_token=token, expires_in=expires_in)


@app.get("/api/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get the current authenticated user."""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        full_name=current_user.get("full_name"),
        created_at=current_user["created_at"],
        is_active=current_user["is_active"]
    )


# --- History Routes ---

class HistoryItem(BaseModel):
    id: int
    lang: str
    hint: Optional[str]
    image_count: int
    image_filenames: List[str]
    product_type: Optional[str]
    brand: Optional[str]
    created_at: Any
    generation_time_ms: Optional[int]


class HistoryListResponse(BaseModel):
    items: List[HistoryItem]
    total: int
    page: int
    limit: int


class HistoryDetailResponse(BaseModel):
    id: int
    lang: str
    hint: Optional[str]
    image_count: int
    image_filenames: List[str]
    result_json: Dict[str, Any]
    product_type: Optional[str]
    brand: Optional[str]
    created_at: Any
    generation_time_ms: Optional[int]


@app.get("/api/history", response_model=HistoryListResponse)
async def list_history(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Get paginated generation history for the current user."""
    offset = (page - 1) * limit
    items, total = get_user_history(current_user["id"], limit=limit, offset=offset)

    return HistoryListResponse(
        items=[HistoryItem(**item) for item in items],
        total=total,
        page=page,
        limit=limit
    )


@app.get("/api/history/{history_id}", response_model=HistoryDetailResponse)
async def get_history_detail(
    history_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a single generation with full result JSON."""
    item = get_generation_by_id(history_id, current_user["id"])

    if item is None:
        raise HTTPException(status_code=404, detail="Generation not found")

    return HistoryDetailResponse(**item)


@app.delete("/api/history/{history_id}")
async def delete_history_item(
    history_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Delete a generation from history."""
    deleted = delete_generation(history_id, current_user["id"])

    if not deleted:
        raise HTTPException(status_code=404, detail="Generation not found")

    return {"ok": True}


# --- Generate Route ---

@app.post("/api/generate", response_model=ListingBundle)
async def generate(
    lang: Lang = Form(...),
    files: List[UploadFile] = File(...),
    hint: Optional[str] = Form(None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="No images provided")


    if len(files) > MAX_IMAGES:
        files = files[:MAX_IMAGES]

    # Capture filenames before reading
    image_filenames = [f.filename or f"image_{i}" for i, f in enumerate(files)]

    content_parts = []
    prompt = build_prompt(lang=lang, hint=hint or "")
    content_parts.append({"type": "text", "text": prompt})

    for f in files:
        data = await f.read()
        if not data:
            continue
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": to_data_url(data, mime=f.content_type or "image/jpeg")}
        })

    if len(content_parts) < 2:
        raise HTTPException(status_code=400, detail="Images are empty or unsupported")

    start_time = time.time()

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": content_parts}],
            response_format={"type": "json_object"},
            max_tokens=4096,
        )
        text = resp.choices[0].message.content
        data = json.loads(text)
        bundle = ListingBundle.model_validate(data)

        # Save to history if user is authenticated
        generation_time_ms = int((time.time() - start_time) * 1000)
        if current_user:
            try:
                save_generation(
                    user_id=current_user["id"],
                    lang=lang,
                    hint=hint,
                    image_count=len(files),
                    image_filenames=image_filenames,
                    result_json=bundle.model_dump(),
                    product_type=bundle.universal.product_type,
                    brand=bundle.universal.brand,
                    generation_time_ms=generation_time_ms
                )
            except Exception:
                pass  # Don't fail the request if history save fails

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

            fix_resp = client.chat.completions.create(
                model=MODEL_ID,
                messages=[{"role": "user", "content": fix_prompt}],
                response_format={"type": "json_object"},
                max_tokens=4096,
            )
            fix_text = fix_resp.choices[0].message.content
            fix_data = json.loads(fix_text)
            bundle = ListingBundle.model_validate(fix_data)

            # Save to history if user is authenticated (auto-fix path)
            generation_time_ms = int((time.time() - start_time) * 1000)
            if current_user:
                try:
                    save_generation(
                        user_id=current_user["id"],
                        lang=lang,
                        hint=hint,
                        image_count=len(files),
                        image_filenames=image_filenames,
                        result_json=bundle.model_dump(),
                        product_type=bundle.universal.product_type,
                        brand=bundle.universal.brand,
                        generation_time_ms=generation_time_ms
                    )
                except Exception:
                    pass

            return bundle
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Model output validation failed: {str(e2)}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})
