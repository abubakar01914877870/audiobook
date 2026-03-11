# ─────────────────────────────────────────────
#  Audiobook Translation Makefile
#  Override API URL: make translate-file FILE=... API_URL=http://...
# ─────────────────────────────────────────────

API_URL ?= http://192.168.1.102:5050/translate

# ── Single file ───────────────────────────────
# Usage:
#   make translate-file FILE="traveler_vol_3/chapter_split/chapter_531_Great Acting.pdf" OUT=traveler_vol_3/output/531-540
translate-file:
	python3 run_api_translation.py "$(FILE)" "$(OUT)" --api-url $(API_URL)

# ── Folder ────────────────────────────────────
# Usage:
#   make translate-folder FOLDER=traveler_vol_3/chapter_split OUT=traveler_vol_3/output/531-540
translate-folder:
	python3 run_api_translation.py "$(FOLDER)" "$(OUT)" --api-url $(API_URL)

# ── Vol 3 batches ─────────────────────────────
translate-vol3-483:
	python3 run_api_translation.py "traveler_vol_3/chapter_split/483-490" traveler_vol_3/output/483-490 --api-url $(API_URL)

translate-vol3-491:
	python3 run_api_translation.py "traveler_vol_3/chapter_split/491-500" traveler_vol_3/output/491-500 --api-url $(API_URL)

translate-vol3-501:
	python3 run_api_translation.py "traveler_vol_3/chapter_split/501-510" traveler_vol_3/output/501-510 --api-url $(API_URL)

translate-vol3-511:
	python3 run_api_translation.py "traveler_vol_3/chapter_split/511-530" traveler_vol_3/output/511-530 --api-url $(API_URL)

translate-vol3-531:
	python3 run_api_translation.py "traveler_vol_3/chapter_split" traveler_vol_3/output/531-732 --api-url $(API_URL)

translate-vol3-all: translate-vol3-483 translate-vol3-491 translate-vol3-501 translate-vol3-511 translate-vol3-531

# ── Gemini (local fallback) ───────────────────
translate-gemini-file:
	python3 run_gemini_translation.py "$(FILE)" "$(OUT)"

translate-gemini-folder:
	python3 run_gemini_translation.py "$(FOLDER)" "$(OUT)"

# ── Claude CLI (local) ────────────────────────
translate-claude-file:
	python3 run_claude_translation.py "$(FILE)" "$(OUT)"

translate-claude-folder:
	python3 run_claude_translation.py "$(FOLDER)" "$(OUT)"

# ── PDF tools ─────────────────────────────────
split-pdf:
	python3 split_pdf.py "$(FILE)" --output "$(OUT)"

split-range:
	python3 split_pdf.py "$(FILE)" --start $(START) --end $(END) --output "$(OUT)"

md-to-pdf:
	python3 md_to_pdf.py "$(FILE)"

# ── Utils ─────────────────────────────────────
check-models:
	python3 get_model_stats.py

test-api:
	curl -s -X POST $(API_URL) \
	  -H "Content-Type: application/json" \
	  -d '{"system_prompt":"Translate to Bengali. Output translation only.","text":"Klein opened his eyes. The room was dark."}' | python3 -m json.tool

help:
	@echo ""
	@echo "Usage:"
	@echo "  make translate-file   FILE=path/to/chapter.pdf OUT=output/folder"
	@echo "  make translate-folder FOLDER=path/to/folder    OUT=output/folder"
	@echo "  make translate-vol3-531"
	@echo "  make translate-vol3-all"
	@echo "  make split-pdf        FILE=book.pdf OUT=./chapters"
	@echo "  make split-range      FILE=book.pdf START=483 END=490 OUT=./chapters"
	@echo "  make md-to-pdf        FILE=output/Chapter_001.md"
	@echo "  make test-api"
	@echo "  make check-models"
	@echo ""
	@echo "Override API URL:"
	@echo "  make translate-file FILE=... OUT=... API_URL=http://other-pc:5050/translate"
	@echo ""

.PHONY: translate-file translate-folder \
        translate-vol3-483 translate-vol3-491 translate-vol3-501 translate-vol3-511 translate-vol3-531 translate-vol3-all \
        translate-gemini-file translate-gemini-folder \
        translate-claude-file translate-claude-folder \
        split-pdf split-range md-to-pdf check-models test-api help
