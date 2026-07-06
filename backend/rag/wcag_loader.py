"""
WS-3 : WCAG Knowledge Base Loader

Responsibility:
    1. Parse WCAG 2.1 and 2.2 HTML specifications using BeautifulSoup4
    to extract every success criterion (number, title, level, full text).
    2. Load regulation_mapping.csv to join the regulation data to each criterion.
    3. Combine into a validates list of WCAGCriterion Pydantic Objects.
    4. Persist to data/wcag_criteria.json for the vector store to consume.

How to use: 
    python -m backend.rag.wcag_loader  # from root of the repository
    python wcag_loader.py              # from backend/rag/

Author: Sreekar
Design: Section 2.3 - Section 3 of WCAG Audit Agent : Design Document v1.0
"""
#Imports
from __future__ import annotations
import csv
import json
import logging 
import re
import sys
from pathlib import Path
from typing import Optional 
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field, field_validator

#Logigng
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",   
)
logger = logging.getLogger("wcag_loader")

#Path
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
DATA_DIR = _REPO_ROOT / "data"
WCAG_RAW_DATA = DATA_DIR / "wcag_raw"
WCAG21_HTML= WCAG_RAW_DATA / "wcag21.html"
WCAG22_HTML= WCAG_RAW_DATA / "wcag22.html"
REGULATION_CSV = DATA_DIR / "regulation_mapping.csv"
OUTPUT_JSON = DATA_DIR / "wcag_criteria.json"

#Pydantic Models
class WCAGCriterion(BaseModel):
    """
    A single WCAG success criterion with the metadata required by the audit agents where
        All fields except understanding_url which mau not exist
        for a given criterion are required. 
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    criterion_number: str = Field(
        ...,
        description="Dot-Notation SC number, e.g '1.4.4'",
        pattern = r"^\d+\.\d+\.\d+$",
    )
    criterion_title: str = Field(
        ...,
        description="Human-readable title, e.g 'Contrast (Minimum)'",
        min_length=2,
    )
    conformance_level: str = Field(
        ...,
        description="WCAG conformance level: A,AA,AAA",
        pattern = r"^(A|AA|AAA)$",
    )
    criterion_text:str = Field(
        ...,
        description= "Full normal text of the success criterion",
        min_length=10,
    )
    disability_classes: list[str]=Field(
        default_factory=list,
        description="List of disability categories that the criterion addresses",
    )
    legal_regulations:list[str]=Field(
        default_factory=list,
        description="List of legal frameworks that validate the criterion",
    )
    pillar:str=Field(
        default="",
        description="WCAG Pillar: Perceivable, Operable, Understandable, Robust",
    )
    wcag_version:str=Field(
        default="2.2",
        description="Least version of WCAG that satisfies the criterion",
    )
    understanding_url: Optional[str]=Field(
        default=None,
        description="Link to WCAG understanding document",
    )

    @field_validator("disability_classes",mode="before")
    @classmethod
    def parse_disability_classes(cls,v:object)->list[str]:
        """Accept either a comma-separated string or list"""
        if isinstance(v,str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v or []
    
    @field_validator("legal_regulations",mode="before")
    @classmethod
    def parse_legal_regulations(cls,v:object)->list[str]:
        """Accept either a comma-separated string or list"""
        if isinstance(v,str):
            return [s.strip() for s in v.split(",")  if s.strip()]
        return v or []
    
class LoaderResult(BaseModel):
    """
    Summary that is returned after a succesful run
    """
    total_criteria:int
    level_A_count:int
    level_AA_count:int
    level_AAA_count:int
    output_path:str
    sources_parsed:list[str]
    warnings:list[str]=Field(default_factory=list)

#Regulation Mapping
def load_regulation_mapping(csv_path:Path)->dict[str,dict]:
    """
    Parse regulation_mapping.csv into a dict that has the criterion_number as the key.

    Returns:
        dict[str,dict]
            Keys are criterion numbers.
            Values are dictionaries with keys: disability_classes, legal_regulations,
            pillar, conformance_level, notes.

        Raises:
            FileNotFoundError: 
                If the csv does not exist at the csv_path.
            ValueError:
                If the csv is missing required header columns.
    """
    if not csv_path.exists():
        return FileNotFoundError(
            f"Regulation mapping CSV not found at {csv_path}\n"
            "Expected Location: data/regulation_mapping.csv"
        )
    logger.info("Loading regulation mapping from %s", csv_path)
    required_columns = {
        "criterion_number", "disability_classes","pillar","ada_title_iii",
        "section_508","eaa_article","psbar_uk","rpwd_india_section","aoda_canada","cvaa_usa",
    }
    mapping:dict[str,dict]={}
    warnings:list[str]=[]
    with csv_path.open(newline="",encoding="utf-8") as f:
        reader=csv.DictReader(f)
        #Header validation
        if reader.fieldnames is None:
            raise ValueError("CSV file is empty")
        missing = required_columns - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"regulation_mapping.csv is missing columns: {missing}\n"
            )
        for row in reader:
            criterion_number = row["criterion_number"].strip()
            if not criterion_number:
                continue
            legal_regs: list[str]=[]
            if row.get("ada_title_iii","").upper() == "TRUE":
                legal_regs.append("ADA Title III")
            if row.get("section_508","").upper() == "TRUE":
                legal_regs.append("Section 508")
            if row.get("eaa_article","").upper() == "TRUE":
                legal_regs.append("EU EAA Article 4")
            if row.get("psbar_uk","").upper() == "TRUE":
                legal_regs.append("UK PSBAR 2018")
            if row.get("equality_act_uk","").upper() == "TRUE":
                legal_regs.append("UK Equality Act 2010")
            if row.get("rpwd_india_section","").upper() == "TRUE":
                legal_regs.append("India RPWD Act Section 46")
            if row.get("aoda_canada","").upper() == "TRUE":    
                legal_regs.append("Canada AODA")
            if row.get("cvaa_usa","").upper() == "TRUE":
                legal_regs.append("USA CVAA")

            raw_dc = row.get("disability_classes","")
            disability_classes = [
                dc.strip() for dc in raw_dc.split(",") if dc.strip()
            ]
            mapping[criterion_number]={
                "disability_classes":disability_classes,
                "legal_regulations":legal_regs,
                "pillar":row.get("pillar","").strip(),
                "conformance_level":row.get("conformance_level","A").strip() or "A",
                "notes": row.get("notes","").strip(),
            }
    logger.info("Loaded %d criterion mappings from CSV", len(mapping))
    return mapping

#HTML Parser
_SC_NUMBER_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b") #Regex for detection of criterion numbers
_LEVEL_PATTERN = re.compile(r"\(Level\s+(A{1,3})\)",re.IGNORECASE) #WCAG conformance levels as in the html files

def _clean_text(text:str)->str:
    """
    Normalize whitespace in the text that is extracted
    """
    return re.sub(r"\s+"," ",text).strip()

def _extract_criterion_text(sc_section:Tag)->str:
    """
    Given a BeautifulSoup Tag that contains the success criterion text,
    exttract the text and normalize whitespace.

    Strategy:
        1. Look for a <p> inside the section that contains the Level badge
        which is usually the first paragraph of the criterion text.
        2. Fall back to the first <p> in the section.
        3. Strip the "Level X" portion from the text as it is stored separately.
    
    Paramters: 
        sc_section: Tag
            The BS4 Tag for the SC's section/div element.

    Returns:
        str
            Cleaned normative text or empty string if not found.
    """
    paragraphs = sc_section.find_all("p",recursive=True)
    for para in paragraphs:
        text = _clean_text(para.get_text())
        if _LEVEL_PATTERN.search(text):
            text = _LEVEL_PATTERN.sub("",text).strip()
            text = _SC_NUMBER_RE.sub("",text,count=1).strip()
            if len(text) > 15:
                return text
    for para in paragraphs:
        text = _clean_text(para.get_text())
        if len(text)>20:
            return text
    return ""

def _extract_conformance_level(section_text:str)->str:
    """
    Extract A/AA/AAA from the raw text of a criterion section.

    Parameters:
    section_text: str
        Raw text content of the criterion section element

    Returns:
    str
        "A","AA","AAA". Returns "A" as safe default if not found
    """
    match = _LEVEL_PATTERN.search(section_text)
    if match:
        return match.group(1).upper()
    return "A"

def parse_wcag_html(
        html_path:Path,
        wcag_version: str,
)->list[dict]:
    """
    Parse a downloadeded WCAG specification HTML file and extract all success
    criteria as raw dictionaries.

    All elements starting with "sc-" or matching with "X.Y.Z" are accounted for to handle both
    WCAG2.1 and WCAG2.2.

    Parameters:
        html_path: Path
            Path to the downloaded html file

        wcag_version: str
            "2.1" or "2.2"

    Returns
        list[dict]
            List of raw criterion dicts with keys:
            criterion_number, criterion_title, conformance_level,
            criterion_text, wcag_version, understanding_url.
    """
    if not html_path.exists():
        logger.warning(
            "WCAG %s HTML is not found at %s",wcag_version,html_path
        )
        return []
    logger.info("Parsing WCAG %s from Path %s",wcag_version, html_path)
    with html_path.open(encoding="utf-8",errors="replace") as f:
        soup = BeautifulSoup(f,"lxml")
    criteria:list[dict]=[]
    seen_numbers: set[str]=set()

    #Strategy 1: Looking for <section class ="sc"> or id starting with "sc-"
    sc_sections = soup.find_all(
        lambda tag: (
            tag.name in ('section','div','li')
            and (
                "sc" in tag.get("class",[])
                or str(tag.get("id","")).startswith("sc-")
                or str(tag.get("id","")).startswith("success-criterion-")
            )
        )
    )
    for section in sc_sections:
        heading_tag = section.find(re.compile(r"^h[2-6]$"))
        if not heading_tag:
            continue

        heading_text = _clean_text(heading_tag.get_text())
        num_match = _SC_NUMBER_RE.search(heading_text)
        if not num_match:
            continue
        sc_number = num_match.group(1)
        if sc_number in seen_numbers:
            continue
        
        #Extracting the title
        title = heading_text
        title = _SC_NUMBER_RE.sub("",title,count=1)
        title = _LEVEL_PATTERN.sub("",title)
        title = _clean_text(title)
        if not title:
            title = f"Success Criterion {sc_number}"

        #Extracting the conformance level
        level = _extract_conformance_level(section.get_text())

        #Extracting the content
        criterion_text=_extract_criterion_text(section)
        if not criterion_text:
            logger.debug("No criterion text found for %s",sc_number)
            continue
        slug = re.sub(r"[^a-z0-9]+",title.lower()).strip("-")
        understanding_url = (
            f"https://www.w3.org/WAI/WCAG{wcag_version.replace('.','')}/"
            f"Understanding/{slug}.html"
        )
        seen_numbers.add(sc_number)
        criteria.append({
            "criterion_number":sc_number,
            "criterion_title":title,
            "conformance_level":level,
            "criterion_text":criterion_text,
            "wcag_version":wcag_version,
            "understanding_url":understanding_url,
        })

    #Strategy 2: Fallback - search ALL headings for SC number pattern
    if not criteria:
        logger.info(
            "Strategy 1 found 0 criteria for WCAG %s",wcag_version,
        )
        for heading in soup.find_all(re.compile(r"6h[2-6]$")):
            heading_text = _clean_text(heading.get_text())
            num_match = _SC_NUMBER_RE.search(heading_text)
            if not num_match:
                continue
            sc_number = num_match.group(1)
            if sc_number in seen_numbers:
                continue
            #Collecting sibling or child paragraphs as the criterion text
            criterion_text_parts: list[str] = []
            level = "A"
            for sibling in heading.next_siblings:
                if not isinstance(sibling,Tag):
                    continue
                if sibling.name and re.match(r"^h[1-6]$",sibling.name):
                    break
                if sibling.name=="p":
                    sib_text = _clean_text(sibling.get_text())
                    lv_match = _LEVEL_PATTERN.search(sib_text)
                    if lv_match:
                        level = lv_match.group(1).upper()
                    criterion_text_parts.append(
                        _LEVEL_PATTERN.sub("",sib_text).strip()
                    )
            criterion_text = "".join(criterion_text_parts).strip()
            if not criterion_text or len(criterion_text)<10:
                continue
            title = heading_text
            title = _SC_NUMBER_RE.sub("",title,count=1)
            title = _LEVEL_PATTERN.sub("",title)
            title = _clean_text(title) or f"Success Criteria {sc_number}"

            slug = re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")
            understanding_url=(
                f"https://www.w3.org/WAI/WCAG{wcag_version.replace(".","")}/"
                f"Understanding/{slug}.html"
            )
            seen_numbers.add(sc_number)
            criteria.append({
                "criterion_number":sc_number,
                "criterion_title":title,
                "conformance_level":level,
                "criterion_text":criterion_text,
                "wcag_version":wcag_version,
                "understanding_url":understanding_url,
            })
    logger.info(
        "Extracted %d success criteria from WCAG %s", len(criteria), wcag_version
    )
    return criteria

#Merging and validation
def _merge_and_deduplicate(
        wcag21_raw: list[dict],
        wcag22_raw: list[dict],
        regulation_map: dict[str,dict],
) -> tuple[list[WCAGCriterion],list[str]]:
    """
    Merge raw criteria form WCAG 2.1 and 2.2, deduplicate by criterion number
    ,join regulation data and validate using Pydantic.

    Parameters: 
        wcag21_raw: list[dict]
            Raw criteria parsed from wcag21.html
        wcag22_raw: list[dict]
            Raw criteria parsed from wcag22.html
        regulation_map: dict[str,dict]
            Mapping from criterion_number to regulation data

    Returns: 
        tuple[list[WCAGCriterion],list[str]]
            (validated_criteria, list_of_warning_messages)
    """
    warnings: list[str] = []
    merged: dict[str,dict] ={}
    for raw in wcag21_raw:
        merged[raw["criterion_number"]] = raw
    for raw in wcag22_raw:
        num = raw['criterion_number']
        for num in merged:
            merged[num]=raw
        else:
            merged[num]=raw

    if not merged:
        logger.warning(
            "No criteria parsed from html files."
            "Falling back to regulation_mapping.csv as the criterion source"
        )
        warnings.append(
            "WCAG HTML files not found. Criterion text is the placeholder."
        )
        for num, reg_data in regulation_map.items():
            merged[num]={
                "criterion_number":num,
                "criterion_title":f"Success Criterion {num}",
                "conformance_level":reg_data.get("conformance_level","A"),
                "criterion_text":(
                    f"[PLACEHOLDER] Download WCAG HTML to populate full text"
                    f"for criterion {num}"
                ),
                "wcag_version":"2.2",
                "understanding_url":None,
            }
    
    #Joining regulation data and validating
    validated: list[WCAGCriterion]=[]
    for num, raw in sorted(merged.items()):
        reg_data=regulation_map.get("num",{})
        # Merge regulation data into the raw dict
        raw["disability_classes"] = reg_data.get("disability_classes", [])
        raw["legal_regulations"] = reg_data.get("legal_regulations", [])
        raw["pillar"] = reg_data.get("pillar", "")
        if not reg_data:
            warnings.append(
                f"Criterion {num} has no entry in regulation_mapping.csv — "
                "disability_classes and legal_regulations will be empty."
            )
        try:
            criterion = WCAGCriterion(**raw)
            validated.append(criterion)
        except Exception as exc:
            warnings.append(
                f"Validation failed for criterion {num}: {exc} — skipped."
            )
            logger.warning("Validation error for %s: %s", num, exc)

    return validated, warnings

# ─── Public entry point ───────────────────────────────────────────────────────

def build_wcag_criteria(
    wcag21_path: Path = WCAG21_HTML,
    wcag22_path: Path = WCAG22_HTML,
    regulation_csv_path: Path = REGULATION_CSV,
    output_path: Path = OUTPUT_JSON,
) -> LoaderResult:
    """
    Full pipeline: parse HTML → load CSV → merge → validate → save JSON.

    This is the function called by the vector store builder and can also
    be called directly in tests with custom paths.

    Parameters
    ----------
    wcag21_path : Path
        Path to the downloaded WCAG 2.1 HTML spec file.
    wcag22_path : Path
        Path to the downloaded WCAG 2.2 HTML spec file.
    regulation_csv_path : Path
        Path to regulation_mapping.csv.
    output_path : Path
        Destination path for the output wcag_criteria.json.

    Returns
    -------
    LoaderResult
        Summary of what was built, including counts and any warnings.

    Raises
    ------
    FileNotFoundError
        If regulation_mapping.csv does not exist (it is mandatory).
    """
    logger.info("=" * 60)
    logger.info("WCAG Criteria Loader — starting build")
    logger.info("=" * 60)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Load regulation mapping (mandatory)
    regulation_map = load_regulation_mapping(regulation_csv_path)

    # Step 2: Parse HTML files (gracefully handles missing files)
    wcag21_raw = parse_wcag_html(wcag21_path, wcag_version="2.1")
    wcag22_raw = parse_wcag_html(wcag22_path, wcag_version="2.2")

    sources_parsed: list[str] = []
    if wcag21_raw:
        sources_parsed.append(str(wcag21_path))
    if wcag22_raw:
        sources_parsed.append(str(wcag22_path))

    # Step 3: Merge, deduplicate, join regulation data, validate
    criteria, warnings = _merge_and_deduplicate(
        wcag21_raw, wcag22_raw, regulation_map
    )

    if not criteria:
        raise RuntimeError(
            "No valid WCAG criteria produced. "
            "Check that regulation_mapping.csv exists and WCAG HTML files are "
            "downloadable. See warnings above."
        )

    # Step 4: Serialize to JSON
    output_data = [c.model_dump() for c in criteria]
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Step 5: Build and return result summary
    level_counts = {"A": 0, "AA": 0, "AAA": 0}
    for c in criteria:
        level_counts[c.conformance_level] = (
            level_counts.get(c.conformance_level, 0) + 1
        )

    result = LoaderResult(
        total_criteria=len(criteria),
        level_a_count=level_counts.get("A", 0),
        level_aa_count=level_counts.get("AA", 0),
        level_aaa_count=level_counts.get("AAA", 0),
        output_path=str(output_path),
        sources_parsed=sources_parsed if sources_parsed else ["regulation_mapping.csv (fallback)"],
        warnings=warnings,
    )

    logger.info("=" * 60)
    logger.info("Build complete: %d criteria saved to %s", result.total_criteria, output_path)
    logger.info("  Level A:   %d", result.level_a_count)
    logger.info("  Level AA:  %d", result.level_aa_count)
    logger.info("  Level AAA: %d", result.level_aaa_count)
    if warnings:
        logger.warning("%d warnings during build:", len(warnings))
        for w in warnings:
            logger.warning("  ⚠ %s", w)
    logger.info("=" * 60)

    return result


# ─── Quick lookup helper (used by vector_store.py) ───────────────────────────

def load_criteria_from_json(json_path: Path = OUTPUT_JSON) -> list[WCAGCriterion]:
    """
    Load already-built wcag_criteria.json into validated WCAGCriterion objects.

    This is the fast path used at runtime by the vector store — it reads
    the pre-built JSON rather than re-parsing the HTML every startup.

    Parameters
    ----------
    json_path : Path
        Path to wcag_criteria.json produced by build_wcag_criteria().

    Returns
    -------
    list[WCAGCriterion]

    Raises
    ------
    FileNotFoundError
        If the JSON has not been built yet. Run build_wcag_criteria() first.
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"wcag_criteria.json not found at {json_path}.\n"
            "Run: python -m backend.rag.wcag_loader\n"
            "to build the criteria database before starting the vector store."
        )

    logger.info("Loading criteria from pre-built JSON: %s", json_path)
    with json_path.open(encoding="utf-8") as f:
        raw_list = json.load(f)

    criteria = [WCAGCriterion(**item) for item in raw_list]
    logger.info("Loaded %d criteria from JSON", len(criteria))
    return criteria


# ─── CLI entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run this script directly to build wcag_criteria.json.

    From repo root:
        python -m backend.rag.wcag_loader

    From backend/rag/:
        python wcag_loader.py

    Optional: pass custom paths as positional arguments:
        python wcag_loader.py /path/to/wcag21.html /path/to/wcag22.html
    """
    # Allow overriding paths via CLI args for testing
    custom_wcag21 = Path(sys.argv[1]) if len(sys.argv) > 1 else WCAG21_HTML
    custom_wcag22 = Path(sys.argv[2]) if len(sys.argv) > 2 else WCAG22_HTML

    try:
        result = build_wcag_criteria(
            wcag21_path=custom_wcag21,
            wcag22_path=custom_wcag22,
        )
        print("\n✅ Build successful")
        print(f"   Total criteria : {result.total_criteria}")
        print(f"   Level A        : {result.level_a_count}")
        print(f"   Level AA       : {result.level_aa_count}")
        print(f"   Level AAA      : {result.level_aaa_count}")
        print(f"   Output         : {result.output_path}")
        if result.warnings:
            print(f"\n⚠  {len(result.warnings)} warning(s):")
            for w in result.warnings:
                print(f"   - {w}")
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n❌ File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during build")
        print(f"\n❌ Build failed: {e}", file=sys.stderr)
        sys.exit(2)
