# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered product listing generator that converts product photos into marketplace listings for OLX, Wildberries, and Ozon. Supports Russian, Kazakh, and English output languages.

## Tech Stack

- **Backend**: FastAPI + Uvicorn (Python)
- **Frontend**: Vanilla JavaScript (single HTML file, no build tools)
- **AI**: OpenAI API with vision capabilities

## Commands

```bash
# Install backend dependencies
pip install -r backend/requierments.txt

# Run development server (from backend directory)
cd backend
uvicorn main:app --reload

# Run production server
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Environment Variables

Create `.env` in the backend directory:

- `OPENAI_API_KEY` (required): OpenAI API key
- `OPENAI_MODEL` (optional): Model ID, defaults to "gpt-4.1-mini"
- `MAX_IMAGES` (optional): Max images per request, defaults to 8

## Architecture

### Data Flow

1. Frontend sends 1-8 product images + language + optional hint to `/api/generate`
2. Backend converts images to base64 data URLs
3. OpenAI vision model extracts product attributes and generates marketplace-specific listings
4. Response validated against Pydantic models; auto-fix attempted if validation fails
5. Returns structured JSON with universal product data + 3 marketplace variants

### Backend Structure (backend/main.py)

- **Pydantic Models**: `UniversalProduct`, `ListingVariant`, `MarketplacePack`, `ListingBundle`
- **Main Endpoint**: `POST /api/generate` - accepts multipart form with `lang`, `files[]`, `hint`
- **Frontend Routes**: `/`, `/olx`, `/wb`, `/ozon` - all serve the same SPA
- **Auto-fix Pattern**: If JSON parsing or Pydantic validation fails, sends error back to OpenAI for correction attempt

### Frontend Structure (frontend/index.html)

- Single-page app with embedded CSS and JS
- State managed via global `state` object
- URL-based mode switching via `history.pushState()` (OLX/Wildberries/Ozon tabs)
- Drag-drop file upload with preview grid

### Key Types

```
Marketplace = Literal["olx", "wildberries", "ozon"]
Lang = Literal["ru", "kz", "en"]
```

## API Response Structure

```json
{
  "lang": "ru|kz|en",
  "universal": {
    "product_type": "...",
    "brand": "...",
    "key_attributes": [...],
    "detected_text": [...],
    "uncertainty": [...]
  },
  "listings": {
    "olx": { "title", "bullets", "description", "keywords", "attributes", "compliance_todos", "uncertainty" },
    "wildberries": { ... },
    "ozon": { ... }
  }
}
```
