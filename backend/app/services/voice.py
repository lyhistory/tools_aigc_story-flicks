import os
import io
import asyncio
import time
import uuid
import json
import edge_tts
import re
import xml.sax.saxutils
from edge_tts import SubMaker, submaker
from edge_tts.submaker import mktimestamp
from moviepy.video.tools import subtitles
from loguru import logger
from typing import Tuple
from xml.sax.saxutils import unescape
from fake_useragent import UserAgent
import websockets
from functools import partial, lru_cache
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from gtts import gTTS
from google.cloud import texttospeech
from pathlib import Path
from app.config import get_settings
import io
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

ua = UserAgent()  # global fake-useragent instance
KARAOKE_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+")

PUNCTUATIONS = [
    "?",
    ",",
    ".",
    "、",
    ";",
    ":",
    "!",
    "…",
    "？",
    "，",
    "。",
    "、",
    "；",
    "：",
    "！",
    "...",
]

def split_string_by_punctuations(s, split_minor_punct: bool = True):
    result = []
    txt = ""
    if split_minor_punct:
        active_punctuations = PUNCTUATIONS
    else:
        # Subtitle segmentation should prefer sentence-level chunks to reduce
        # visible lag (avoid splitting on commas/colons/semicolons).
        active_punctuations = ["?", ".", "!", "…", "？", "。", "！", "..."]

    previous_char = ""
    next_char = ""
    for i in range(len(s)):
        char = s[i]
        if char == "\n":
            if txt.strip():  # 只有在非空的情况下才添加
                result.append(txt.strip())
            txt = ""
            continue

        if i > 0:
            previous_char = s[i - 1]
        if i < len(s) - 1:
            next_char = s[i + 1]

        if char == "." and previous_char.isdigit() and next_char.isdigit():
            txt += char
            continue

        if char not in active_punctuations:
            txt += char
        else:
            if txt.strip():  # 只有在非空的情况下才添加
                result.append(txt.strip())
            txt = ""
    if txt.strip():  # 最后一段如果非空也要添加
        result.append(txt.strip())
    
    # 过滤掉空字符串和只包含标点符号的片段
    def is_valid_segment(segment):
        # 去掉所有标点符号后还有内容的才是有效片段
        return bool(re.sub(r'[^\w\s]', '', segment).strip())
    
    result = list(filter(is_valid_segment, result))
    return result


def split_text_for_subtitles(s: str) -> list[str]:
    return split_string_by_punctuations(s, split_minor_punct=False)

def sanitize_text_for_tts(raw_text: str) -> str:
    if not raw_text:
        return raw_text
    cleaned = (
        str(raw_text)
        .replace("\\u200b", "")
        .replace("\\u200c", "")
        .replace("\\u200d", "")
        .replace("\\ufeff", "")
        .replace("\\u2060", "")
        .replace("​", "")
        .replace("‌", "")
        .replace("‍", "")
        .replace("\ufeff", "")
        .replace("\u2060", "")
    )
    cleaned = cleaned.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    cleaned = re.sub(r"(?<!\w)['\"](?!\w)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def expand_contractions(raw_text: str) -> str:
    if not raw_text:
        return raw_text
    replacements = {
        "we're": "we are",
        "you're": "you are",
        "they're": "they are",
        "I'm": "I am",
        "i'm": "I am",
        "it's": "it is",
        "that's": "that is",
        "there's": "there is",
        "can't": "cannot",
        "don't": "do not",
        "doesn't": "does not",
        "isn't": "is not",
        "won't": "will not",
        "let's": "let us",
    }
    text = raw_text
    for k, v in replacements.items():
        text = re.sub(rf"\\b{re.escape(k)}\\b", v, text)
    return text

def karaoke_tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in KARAOKE_WORD_PATTERN.finditer(text)]

def _write_karaoke_words_file(
    subtitle_file: str,
    provider: str,
    words: list[dict],
    *,
    timing_source: str = "unknown",
    timing_quality: str = "approx",
) -> None:
    words_file = os.path.splitext(subtitle_file)[0] + ".words.json"
    payload = {
        "provider": provider,
        "timing_source": timing_source,
        "timing_quality": timing_quality,
        "words": words,
    }
    with open(words_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(
        f"Karaoke words saved: {words_file} ({len(words)} words) "
        f"| source={timing_source} quality={timing_quality}"
    )

def _karaoke_words_from_subtitle(subtitle_file: str) -> list[dict]:
    words: list[dict] = []
    try:
        sbs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
        for ((start_s, end_s), line_text) in sbs:
            tokens = karaoke_tokenize(line_text or "")
            if not tokens:
                continue
            line_start = float(start_s)
            line_end = float(end_s)
            line_dur = max(0.05, line_end - line_start)
            step = line_dur / len(tokens)
            for idx, token in enumerate(tokens):
                ws = line_start + idx * step
                we = line_end if idx == len(tokens) - 1 else line_start + (idx + 1) * step
                words.append(
                    {
                        "word": token,
                        "start": round(ws, 4),
                        "end": round(max(ws + 0.03, we), 4),
                    }
                )
    except Exception as e:
        logger.warning(f"Failed to build fallback karaoke timings from subtitle: {e}")
    return words

# ---------------------------------------------------------------------------
# Whisper forced-alignment helper
# ---------------------------------------------------------------------------
_whisper_model_cache: dict = {}  # model_size -> WhisperModel instance

def _get_whisper_model(model_size: str = "tiny"):
    """Lazy-load and cache a faster-whisper model (CPU, int8)."""
    if model_size not in _whisper_model_cache:
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Loading faster-whisper model '{model_size}' (first load, may download ~75MB)…")
            _whisper_model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
            logger.info(f"faster-whisper model '{model_size}' ready")
        except Exception as e:
            logger.warning(f"faster-whisper unavailable: {e}")
            _whisper_model_cache[model_size] = None
    return _whisper_model_cache[model_size]


def _whisper_align_words(audio_path: str, expected_text: str, model_size: str = "tiny") -> list[dict] | None:
    """
    Transcribe *audio_path* with faster-whisper and return word-level timings.

    Returns a list of {"word": str, "start": float, "end": float} dicts that
    match the tokens in *expected_text*, or None if Whisper is unavailable or
    the transcription produces no words.
    """
    model = _get_whisper_model(model_size)
    if model is None:
        return None
    try:
        segments, _ = model.transcribe(
            audio_path,
            word_timestamps=True,
            language="en",
            beam_size=1,         # fastest inference
            vad_filter=False,    # we trust the audio to be speech
        )
        words: list[dict] = []
        for seg in segments:
            for w in (seg.words or []):
                token = (w.word or "").strip()
                if token:
                    words.append({
                        "word": token,
                        "start": round(float(w.start), 4),
                        "end": round(float(w.end), 4),
                    })
        if not words:
            logger.warning("faster-whisper returned 0 words for the audio")
            return None
        logger.info(f"faster-whisper aligned {len(words)} words from '{audio_path}'")
        return words
    except Exception as e:
        logger.warning(f"faster-whisper alignment failed: {e}")
        return None


def _build_google_ssml_with_marks(text: str) -> tuple[str, list[str]]:

    marks: list[str] = []
    parts: list[str] = []
    last = 0
    token_index = 0
    for m in KARAOKE_WORD_PATTERN.finditer(text or ""):
        if m.start() > last:
            parts.append(xml.sax.saxutils.escape(text[last:m.start()]))
        parts.append(f'<mark name="w{token_index}"/>')
        token = m.group(0)
        parts.append(xml.sax.saxutils.escape(token))
        marks.append(token)
        token_index += 1
        last = m.end()
    if text and last < len(text):
        parts.append(xml.sax.saxutils.escape(text[last:]))
    return f"<speak>{''.join(parts)}</speak>", marks

def build_srt_from_audio(audio: AudioSegment, clean_text: str, subtitle_file: str) -> None:
    total_duration_ms = len(audio)
    total_duration_s = total_duration_ms / 1000.0
    nonsilent_chunks = detect_nonsilent(audio, min_silence_len=400, silence_thresh=-40)
    lines = split_text_for_subtitles(clean_text)
    num_lines = len(lines)

    def normalize_srt_times(time_pairs, total_s, min_gap=0.05, min_dur=0.25):
        normalized = []
        prev_end = 0.0
        for start_s, end_s in time_pairs:
            start_s = max(0.0, float(start_s))
            end_s = max(float(end_s), start_s)
            if start_s < prev_end + min_gap:
                start_s = prev_end + min_gap
            if end_s < start_s + min_dur:
                end_s = start_s + min_dur
            if end_s > total_s:
                end_s = total_s
                if end_s - start_s < min_dur:
                    start_s = max(0.0, end_s - min_dur)
                    if start_s < prev_end + min_gap:
                        start_s = prev_end + min_gap
                        end_s = min(total_s, start_s + min_dur)
            normalized.append((start_s, end_s))
            prev_end = end_s
        if normalized and normalized[0][0] > 0.2:
            shift = min(0.2, normalized[0][0] - 0.05)
            shifted = []
            for start_s, end_s in normalized:
                start_s = max(0.0, start_s - shift)
                end_s = max(start_s + min_dur, end_s - shift)
                shifted.append((start_s, min(end_s, total_s)))
            normalized = shifted
        return normalized

    if num_lines == 0:
        logger.warning("No lines for SRT")
        with open(subtitle_file, "w", encoding="utf-8") as f:
            f.write("")
        return

    if len(nonsilent_chunks) >= num_lines:
        with open(subtitle_file, "w", encoding="utf-8") as f:
            raw_pairs = []
            for i in range(num_lines):
                start_ms, end_ms = nonsilent_chunks[i]
                raw_pairs.append((start_ms / 1000.0, end_ms / 1000.0))
            norm_pairs = normalize_srt_times(raw_pairs, total_duration_s)
            for i, line in enumerate(lines, 1):
                start_s, end_s = norm_pairs[i - 1]
                start_hms = f"{int(start_s // 3600):02d}:{int((start_s % 3600) // 60):02d}:{int(start_s % 60):02d},{int((start_s % 1)*1000):03d}"
                end_hms   = f"{int(end_s // 3600):02d}:{int((end_s % 3600) // 60):02d}:{int(end_s % 60):02d},{int((end_s % 1)*1000):03d}"
                f.write(f"{i}\n")
                f.write(f"{start_hms} --> {end_hms}\n")
                f.write(f"{line.strip()}\n\n")
        logger.info("SRT aligned with real speech chunks")
        return

    time_per_line = (total_duration_s - 1.0) / num_lines if num_lines > 0 else 5.0
    with open(subtitle_file, "w", encoding="utf-8") as f:
        current_time = 0.5
        raw_pairs = []
        for _ in range(num_lines):
            start_s = current_time
            end_s = min(start_s + time_per_line, total_duration_s - 0.5)
            raw_pairs.append((start_s, end_s))
            current_time = end_s
        norm_pairs = normalize_srt_times(raw_pairs, total_duration_s)
        for i, line in enumerate(lines, 1):
            start_s, end_s = norm_pairs[i - 1]
            start_hms = f"{int(start_s // 3600):02d}:{int((start_s % 3600) // 60):02d}:{int(start_s % 60):02d},{int((start_s % 1)*1000):03d}"
            end_hms   = f"{int(end_s // 3600):02d}:{int((end_s % 3600) // 60):02d}:{int(end_s % 60):02d},{int((end_s % 1)*1000):03d}"
            f.write(f"{i}\n")
            f.write(f"{start_hms} --> {end_hms}\n")
            f.write(f"{line.strip()}\n\n")
    logger.info("SRT fallback with silence trim")

def _format_srt_ts(seconds: float) -> str:
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = int(s % 60)
    ms = int((s - int(s)) * 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

def build_srt_from_lines_and_words(clean_text: str, words: list[dict], subtitle_file: str) -> None:
    lines = split_text_for_subtitles(clean_text)
    if not lines:
        with open(subtitle_file, "w", encoding="utf-8") as f:
            f.write("")
        return
        
    def normalize(t: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]", "", str(t)).lower()
        
    word_idx = 0
    with open(subtitle_file, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines, 1):
            line_words = [normalize(m.group(0)) for m in KARAOKE_WORD_PATTERN.finditer(line)]
            line_words = [w for w in line_words if w]
            
            start_s = None
            end_s = None
            
            for lw in line_words:
                matched = False
                for offset in range(3):
                    idx = word_idx + offset
                    if idx < len(words) and normalize(words[idx].get("word", "")) == lw:
                        if start_s is None:
                            start_s = words[idx].get("start", 0.0)
                        end_s = words[idx].get("end", 0.0)
                        word_idx = idx + 1
                        matched = True
                        break
                if not matched and word_idx < len(words):
                    if start_s is None:
                        start_s = words[word_idx].get("start", 0.0)
                    end_s = words[word_idx].get("end", 0.0)
                    word_idx += 1
            
            if start_s is None:
                start_s = 0.0 if not words else float(words[-1].get("end", 0.0))
            if end_s is None or end_s <= start_s:
                end_s = float(start_s) + 2.0
                
            start_s = max(0.0, float(start_s))
            end_s = max(start_s + 0.1, float(end_s))
            
            f.write(f"{i}\n")
            f.write(f"{_format_srt_ts(start_s)} --> {_format_srt_ts(end_s)}\n")
            f.write(f"{line.strip()}\n\n")

def build_srt_from_word_timings(word_timings: list[dict], subtitle_file: str) -> None:
    with open(subtitle_file, "w", encoding="utf-8") as f:
        for idx, item in enumerate(word_timings, 1):
            text = str(item.get("word", "")).strip()
            if not text:
                continue
            start_s = float(item.get("start", 0.0) or 0.0)
            end_s = float(item.get("end", start_s + 0.03) or (start_s + 0.03))
            if end_s <= start_s:
                end_s = start_s + 0.03
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_ts(start_s)} --> {_format_srt_ts(end_s)}\n")
            f.write(f"{text}\n\n")

def get_all_azure_voices(filter_locals=None) -> list[str]:
    if filter_locals is None:
        filter_locals = ["zh-CN", "en-US", "zh-TW", "ja-JP", "ko-KR"]
    voices_str = """
Name: af-ZA-AdriNeural
Gender: Female

Name: af-ZA-WillemNeural
Gender: Male

Name: am-ET-AmehaNeural
Gender: Male

Name: am-ET-MekdesNeural
Gender: Female

Name: ar-AE-FatimaNeural
Gender: Female

Name: ar-AE-HamdanNeural
Gender: Male

Name: ar-BH-AliNeural
Gender: Male

Name: ar-BH-LailaNeural
Gender: Female

Name: ar-DZ-AminaNeural
Gender: Female

Name: ar-DZ-IsmaelNeural
Gender: Male

Name: ar-EG-SalmaNeural
Gender: Female

Name: ar-EG-ShakirNeural
Gender: Male

Name: ar-IQ-BasselNeural
Gender: Male

Name: ar-IQ-RanaNeural
Gender: Female

Name: ar-JO-SanaNeural
Gender: Female

Name: ar-JO-TaimNeural
Gender: Male

Name: ar-KW-FahedNeural
Gender: Male

Name: ar-KW-NouraNeural
Gender: Female

Name: ar-LB-LaylaNeural
Gender: Female

Name: ar-LB-RamiNeural
Gender: Male

Name: ar-LY-ImanNeural
Gender: Female

Name: ar-LY-OmarNeural
Gender: Male

Name: ar-MA-JamalNeural
Gender: Male

Name: ar-MA-MounaNeural
Gender: Female

Name: ar-OM-AbdullahNeural
Gender: Male

Name: ar-OM-AyshaNeural
Gender: Female

Name: ar-QA-AmalNeural
Gender: Female

Name: ar-QA-MoazNeural
Gender: Male

Name: ar-SA-HamedNeural
Gender: Male

Name: ar-SA-ZariyahNeural
Gender: Female

Name: ar-SY-AmanyNeural
Gender: Female

Name: ar-SY-LaithNeural
Gender: Male

Name: ar-TN-HediNeural
Gender: Male

Name: ar-TN-ReemNeural
Gender: Female

Name: ar-YE-MaryamNeural
Gender: Female

Name: ar-YE-SalehNeural
Gender: Male

Name: az-AZ-BabekNeural
Gender: Male

Name: az-AZ-BanuNeural
Gender: Female

Name: bg-BG-BorislavNeural
Gender: Male

Name: bg-BG-KalinaNeural
Gender: Female

Name: bn-BD-NabanitaNeural
Gender: Female

Name: bn-BD-PradeepNeural
Gender: Male

Name: bn-IN-BashkarNeural
Gender: Male

Name: bn-IN-TanishaaNeural
Gender: Female

Name: bs-BA-GoranNeural
Gender: Male

Name: bs-BA-VesnaNeural
Gender: Female

Name: ca-ES-EnricNeural
Gender: Male

Name: ca-ES-JoanaNeural
Gender: Female

Name: cs-CZ-AntoninNeural
Gender: Male

Name: cs-CZ-VlastaNeural
Gender: Female

Name: cy-GB-AledNeural
Gender: Male

Name: cy-GB-NiaNeural
Gender: Female

Name: da-DK-ChristelNeural
Gender: Female

Name: da-DK-JeppeNeural
Gender: Male

Name: de-AT-IngridNeural
Gender: Female

Name: de-AT-JonasNeural
Gender: Male

Name: de-CH-JanNeural
Gender: Male

Name: de-CH-LeniNeural
Gender: Female

Name: de-DE-AmalaNeural
Gender: Female

Name: de-DE-ConradNeural
Gender: Male

Name: de-DE-FlorianMultilingualNeural
Gender: Male

Name: de-DE-KatjaNeural
Gender: Female

Name: de-DE-KillianNeural
Gender: Male

Name: de-DE-SeraphinaMultilingualNeural
Gender: Female

Name: el-GR-AthinaNeural
Gender: Female

Name: el-GR-NestorasNeural
Gender: Male

Name: en-AU-NatashaNeural
Gender: Female

Name: en-AU-WilliamNeural
Gender: Male

Name: en-CA-ClaraNeural
Gender: Female

Name: en-CA-LiamNeural
Gender: Male

Name: en-GB-LibbyNeural
Gender: Female

Name: en-GB-MaisieNeural
Gender: Female

Name: en-GB-RyanNeural
Gender: Male

Name: en-GB-SoniaNeural
Gender: Female

Name: en-GB-ThomasNeural
Gender: Male

Name: en-HK-SamNeural
Gender: Male

Name: en-HK-YanNeural
Gender: Female

Name: en-IE-ConnorNeural
Gender: Male

Name: en-IE-EmilyNeural
Gender: Female

Name: en-IN-NeerjaExpressiveNeural
Gender: Female

Name: en-IN-NeerjaNeural
Gender: Female

Name: en-IN-PrabhatNeural
Gender: Male

Name: en-KE-AsiliaNeural
Gender: Female

Name: en-KE-ChilembaNeural
Gender: Male

Name: en-NG-AbeoNeural
Gender: Male

Name: en-NG-EzinneNeural
Gender: Female

Name: en-NZ-MitchellNeural
Gender: Male

Name: en-NZ-MollyNeural
Gender: Female

Name: en-PH-JamesNeural
Gender: Male

Name: en-PH-RosaNeural
Gender: Female

Name: en-SG-LunaNeural
Gender: Female

Name: en-SG-WayneNeural
Gender: Male

Name: en-TZ-ElimuNeural
Gender: Male

Name: en-TZ-ImaniNeural
Gender: Female

Name: en-US-AnaNeural
Gender: Female

Name: en-US-AndrewMultilingualNeural
Gender: Male

Name: en-US-AndrewNeural
Gender: Male

Name: en-US-AriaNeural
Gender: Female

Name: en-US-AvaMultilingualNeural
Gender: Female

Name: en-US-AvaNeural
Gender: Female

Name: en-US-BrianMultilingualNeural
Gender: Male

Name: en-US-BrianNeural
Gender: Male

Name: en-US-ChristopherNeural
Gender: Male

Name: en-US-EmmaMultilingualNeural
Gender: Female

Name: en-US-EmmaNeural
Gender: Female

Name: en-US-EricNeural
Gender: Male

Name: en-US-GuyNeural
Gender: Male

Name: en-US-JennyNeural
Gender: Female

Name: en-US-MichelleNeural
Gender: Female

Name: en-US-RogerNeural
Gender: Male

Name: en-US-SteffanNeural
Gender: Male

Name: en-ZA-LeahNeural
Gender: Female

Name: en-ZA-LukeNeural
Gender: Male

Name: es-AR-ElenaNeural
Gender: Female

Name: es-AR-TomasNeural
Gender: Male

Name: es-BO-MarceloNeural
Gender: Male

Name: es-BO-SofiaNeural
Gender: Female

Name: es-CL-CatalinaNeural
Gender: Female

Name: es-CL-LorenzoNeural
Gender: Male

Name: es-CO-GonzaloNeural
Gender: Male

Name: es-CO-SalomeNeural
Gender: Female

Name: es-CR-JuanNeural
Gender: Male

Name: es-CR-MariaNeural
Gender: Female

Name: es-CU-BelkysNeural
Gender: Female

Name: es-CU-ManuelNeural
Gender: Male

Name: es-DO-EmilioNeural
Gender: Male

Name: es-DO-RamonaNeural
Gender: Female

Name: es-EC-AndreaNeural
Gender: Female

Name: es-EC-LuisNeural
Gender: Male

Name: es-ES-AlvaroNeural
Gender: Male

Name: es-ES-ElviraNeural
Gender: Female

Name: es-ES-XimenaNeural
Gender: Female

Name: es-GQ-JavierNeural
Gender: Male

Name: es-GQ-TeresaNeural
Gender: Female

Name: es-GT-AndresNeural
Gender: Male

Name: es-GT-MartaNeural
Gender: Female

Name: es-HN-CarlosNeural
Gender: Male

Name: es-HN-KarlaNeural
Gender: Female

Name: es-MX-DaliaNeural
Gender: Female

Name: es-MX-JorgeNeural
Gender: Male

Name: es-NI-FedericoNeural
Gender: Male

Name: es-NI-YolandaNeural
Gender: Female

Name: es-PA-MargaritaNeural
Gender: Female

Name: es-PA-RobertoNeural
Gender: Male

Name: es-PE-AlexNeural
Gender: Male

Name: es-PE-CamilaNeural
Gender: Female

Name: es-PR-KarinaNeural
Gender: Female

Name: es-PR-VictorNeural
Gender: Male

Name: es-PY-MarioNeural
Gender: Male

Name: es-PY-TaniaNeural
Gender: Female

Name: es-SV-LorenaNeural
Gender: Female

Name: es-SV-RodrigoNeural
Gender: Male

Name: es-US-AlonsoNeural
Gender: Male

Name: es-US-PalomaNeural
Gender: Female

Name: es-UY-MateoNeural
Gender: Male

Name: es-UY-ValentinaNeural
Gender: Female

Name: es-VE-PaolaNeural
Gender: Female

Name: es-VE-SebastianNeural
Gender: Male

Name: et-EE-AnuNeural
Gender: Female

Name: et-EE-KertNeural
Gender: Male

Name: fa-IR-DilaraNeural
Gender: Female

Name: fa-IR-FaridNeural
Gender: Male

Name: fi-FI-HarriNeural
Gender: Male

Name: fi-FI-NooraNeural
Gender: Female

Name: fil-PH-AngeloNeural
Gender: Male

Name: fil-PH-BlessicaNeural
Gender: Female

Name: fr-BE-CharlineNeural
Gender: Female

Name: fr-BE-GerardNeural
Gender: Male

Name: fr-CA-AntoineNeural
Gender: Male

Name: fr-CA-JeanNeural
Gender: Male

Name: fr-CA-SylvieNeural
Gender: Female

Name: fr-CA-ThierryNeural
Gender: Male

Name: fr-CH-ArianeNeural
Gender: Female

Name: fr-CH-FabriceNeural
Gender: Male

Name: fr-FR-DeniseNeural
Gender: Female

Name: fr-FR-EloiseNeural
Gender: Female

Name: fr-FR-HenriNeural
Gender: Male

Name: fr-FR-RemyMultilingualNeural
Gender: Male

Name: fr-FR-VivienneMultilingualNeural
Gender: Female

Name: ga-IE-ColmNeural
Gender: Male

Name: ga-IE-OrlaNeural
Gender: Female

Name: gl-ES-RoiNeural
Gender: Male

Name: gl-ES-SabelaNeural
Gender: Female

Name: gu-IN-DhwaniNeural
Gender: Female

Name: gu-IN-NiranjanNeural
Gender: Male

Name: he-IL-AvriNeural
Gender: Male

Name: he-IL-HilaNeural
Gender: Female

Name: hi-IN-MadhurNeural
Gender: Male

Name: hi-IN-SwaraNeural
Gender: Female

Name: hr-HR-GabrijelaNeural
Gender: Female

Name: hr-HR-SreckoNeural
Gender: Male

Name: hu-HU-NoemiNeural
Gender: Female

Name: hu-HU-TamasNeural
Gender: Male

Name: id-ID-ArdiNeural
Gender: Male

Name: id-ID-GadisNeural
Gender: Female

Name: is-IS-GudrunNeural
Gender: Female

Name: is-IS-GunnarNeural
Gender: Male

Name: it-IT-DiegoNeural
Gender: Male

Name: it-IT-ElsaNeural
Gender: Female

Name: it-IT-GiuseppeMultilingualNeural
Gender: Male

Name: it-IT-IsabellaNeural
Gender: Female

Name: iu-Cans-CA-SiqiniqNeural
Gender: Female

Name: iu-Cans-CA-TaqqiqNeural
Gender: Male

Name: iu-Latn-CA-SiqiniqNeural
Gender: Female

Name: iu-Latn-CA-TaqqiqNeural
Gender: Male

Name: ja-JP-KeitaNeural
Gender: Male

Name: ja-JP-NanamiNeural
Gender: Female

Name: jv-ID-DimasNeural
Gender: Male

Name: jv-ID-SitiNeural
Gender: Female

Name: ka-GE-EkaNeural
Gender: Female

Name: ka-GE-GiorgiNeural
Gender: Male

Name: kk-KZ-AigulNeural
Gender: Female

Name: kk-KZ-DauletNeural
Gender: Male

Name: km-KH-PisethNeural
Gender: Male

Name: km-KH-SreymomNeural
Gender: Female

Name: kn-IN-GaganNeural
Gender: Male

Name: kn-IN-SapnaNeural
Gender: Female

Name: ko-KR-HyunsuMultilingualNeural
Gender: Male

Name: ko-KR-InJoonNeural
Gender: Male

Name: ko-KR-SunHiNeural
Gender: Female

Name: lo-LA-ChanthavongNeural
Gender: Male

Name: lo-LA-KeomanyNeural
Gender: Female

Name: lt-LT-LeonasNeural
Gender: Male

Name: lt-LT-OnaNeural
Gender: Female

Name: lv-LV-EveritaNeural
Gender: Female

Name: lv-LV-NilsNeural
Gender: Male

Name: mk-MK-AleksandarNeural
Gender: Male

Name: mk-MK-MarijaNeural
Gender: Female

Name: ml-IN-MidhunNeural
Gender: Male

Name: ml-IN-SobhanaNeural
Gender: Female

Name: mn-MN-BataaNeural
Gender: Male

Name: mn-MN-YesuiNeural
Gender: Female

Name: mr-IN-AarohiNeural
Gender: Female

Name: mr-IN-ManoharNeural
Gender: Male

Name: ms-MY-OsmanNeural
Gender: Male

Name: ms-MY-YasminNeural
Gender: Female

Name: mt-MT-GraceNeural
Gender: Female

Name: mt-MT-JosephNeural
Gender: Male

Name: my-MM-NilarNeural
Gender: Female

Name: my-MM-ThihaNeural
Gender: Male

Name: nb-NO-FinnNeural
Gender: Male

Name: nb-NO-PernilleNeural
Gender: Female

Name: ne-NP-HemkalaNeural
Gender: Female

Name: ne-NP-SagarNeural
Gender: Male

Name: nl-BE-ArnaudNeural
Gender: Male

Name: nl-BE-DenaNeural
Gender: Female

Name: nl-NL-ColetteNeural
Gender: Female

Name: nl-NL-FennaNeural
Gender: Female

Name: nl-NL-MaartenNeural
Gender: Male

Name: pl-PL-MarekNeural
Gender: Male

Name: pl-PL-ZofiaNeural
Gender: Female

Name: ps-AF-GulNawazNeural
Gender: Male

Name: ps-AF-LatifaNeural
Gender: Female

Name: pt-BR-AntonioNeural
Gender: Male

Name: pt-BR-FranciscaNeural
Gender: Female

Name: pt-BR-ThalitaMultilingualNeural
Gender: Female

Name: pt-PT-DuarteNeural
Gender: Male

Name: pt-PT-RaquelNeural
Gender: Female

Name: ro-RO-AlinaNeural
Gender: Female

Name: ro-RO-EmilNeural
Gender: Male

Name: ru-RU-DmitryNeural
Gender: Male

Name: ru-RU-SvetlanaNeural
Gender: Female

Name: si-LK-SameeraNeural
Gender: Male

Name: si-LK-ThiliniNeural
Gender: Female

Name: sk-SK-LukasNeural
Gender: Male

Name: sk-SK-ViktoriaNeural
Gender: Female

Name: sl-SI-PetraNeural
Gender: Female

Name: sl-SI-RokNeural
Gender: Male

Name: so-SO-MuuseNeural
Gender: Male

Name: so-SO-UbaxNeural
Gender: Female

Name: sq-AL-AnilaNeural
Gender: Female

Name: sq-AL-IlirNeural
Gender: Male

Name: sr-RS-NicholasNeural
Gender: Male

Name: sr-RS-SophieNeural
Gender: Female

Name: su-ID-JajangNeural
Gender: Male

Name: su-ID-TutiNeural
Gender: Female

Name: sv-SE-MattiasNeural
Gender: Male

Name: sv-SE-SofieNeural
Gender: Female

Name: sw-KE-RafikiNeural
Gender: Male

Name: sw-KE-ZuriNeural
Gender: Female

Name: sw-TZ-DaudiNeural
Gender: Male

Name: sw-TZ-RehemaNeural
Gender: Female

Name: ta-IN-PallaviNeural
Gender: Female

Name: ta-IN-ValluvarNeural
Gender: Male

Name: ta-LK-KumarNeural
Gender: Male

Name: ta-LK-SaranyaNeural
Gender: Female

Name: ta-MY-KaniNeural
Gender: Female

Name: ta-MY-SuryaNeural
Gender: Male

Name: ta-SG-AnbuNeural
Gender: Male

Name: ta-SG-VenbaNeural
Gender: Female

Name: te-IN-MohanNeural
Gender: Male

Name: te-IN-ShrutiNeural
Gender: Female

Name: th-TH-NiwatNeural
Gender: Male

Name: th-TH-PremwadeeNeural
Gender: Female

Name: tr-TR-AhmetNeural
Gender: Male

Name: tr-TR-EmelNeural
Gender: Female

Name: uk-UA-OstapNeural
Gender: Male

Name: uk-UA-PolinaNeural
Gender: Female

Name: ur-IN-GulNeural
Gender: Female

Name: ur-IN-SalmanNeural
Gender: Male

Name: ur-PK-AsadNeural
Gender: Male

Name: ur-PK-UzmaNeural
Gender: Female

Name: uz-UZ-MadinaNeural
Gender: Female

Name: uz-UZ-SardorNeural
Gender: Male

Name: vi-VN-HoaiMyNeural
Gender: Female

Name: vi-VN-NamMinhNeural
Gender: Male

Name: zh-CN-XiaoxiaoNeural
Gender: Female

Name: zh-CN-XiaoyiNeural
Gender: Female

Name: zh-CN-YunjianNeural
Gender: Male

Name: zh-CN-YunxiNeural
Gender: Male

Name: zh-CN-YunxiaNeural
Gender: Male

Name: zh-CN-YunyangNeural
Gender: Male

Name: zh-CN-liaoning-XiaobeiNeural
Gender: Female

Name: zh-CN-shaanxi-XiaoniNeural
Gender: Female

Name: zh-HK-HiuGaaiNeural
Gender: Female

Name: zh-HK-HiuMaanNeural
Gender: Female

Name: zh-HK-WanLungNeural
Gender: Male

Name: zh-TW-HsiaoChenNeural
Gender: Female

Name: zh-TW-HsiaoYuNeural
Gender: Female

Name: zh-TW-YunJheNeural
Gender: Male

Name: zu-ZA-ThandoNeural
Gender: Female

Name: zu-ZA-ThembaNeural
Gender: Male
    """.strip()
    voices = []
    name = ""
    for line in voices_str.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("Name: "):
            name = line[6:].strip()
        if line.startswith("Gender: "):
            gender = line[8:].strip()
            if name and gender:
                # voices.append({
                #     "name": name,
                #     "gender": gender,
                # })
                if filter_locals:
                    for filter_local in filter_locals:
                        if name.lower().startswith(filter_local.lower()):
                            voices.append(f"{name}-{gender}")
                else:
                    voices.append(f"{name}-{gender}")
                name = ""
    voices.sort()
    return voices


def parse_voice_name(name: str):
    # zh-CN-XiaoyiNeural-Female
    # zh-CN-YunxiNeural-Male
    # zh-CN-XiaoxiaoMultilingualNeural-V2-Female
    name = name.replace("-Female", "").replace("-Male", "").strip()
    return name

def normalize_language_code(language: str) -> str:
    if not language:
        return language
    if language.startswith("fixed-"):
        return language.replace("fixed-", "")
    return language

def _resolve_credentials_path(path_str: str) -> str:
    if not path_str:
        return ""
    if os.path.isabs(path_str) and os.path.exists(path_str):
        return path_str
    if os.path.exists(path_str):
        return os.path.abspath(path_str)
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / path_str
    if candidate.exists():
        return str(candidate)
    backend_root = Path(__file__).resolve().parents[2]
    candidate = backend_root / path_str
    if candidate.exists():
        return str(candidate)
    return path_str

def ensure_google_credentials() -> None:
    settings = get_settings()
    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if env_path and os.path.exists(env_path):
        return
    cred = _resolve_credentials_path(env_path) if env_path else ""
    if not cred:
        cred = _resolve_credentials_path(settings.google_application_credentials)
    if cred:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred
        logger.info(f"Google credentials set: {cred}")

@lru_cache(maxsize=1)
def _google_voices_cached():
    ensure_google_credentials()
    client = texttospeech.TextToSpeechClient()
    return client.list_voices().voices

def list_google_tts_languages(allowed: list[str] | None = None) -> list[str]:
    try:
        ensure_google_credentials()
        langs = set()
        for voice in _google_voices_cached():
            if not _google_voice_tier(voice.name):
                continue
            for lang in voice.language_codes:
                langs.add(lang)
        results = sorted(langs)
    except Exception as e:
        logger.warning(f"Google TTS list languages failed: {e}")
        results = allowed or []
    if allowed:
        results = [lang for lang in results if lang in allowed]
    return results

def list_google_tts_voices(language: str | None = None, allowed: list[str] | None = None) -> list[str]:
    lang = normalize_language_code(language) if language else None
    try:
        ensure_google_credentials()
        voices = []
        for voice in _google_voices_cached():
            if not _google_voice_tier(voice.name):
                continue
            if lang and lang not in voice.language_codes:
                continue
            if allowed and lang and lang not in allowed:
                continue
            voices.append(voice.name)
        def sort_key(v: str):
            tier = _google_voice_tier(v)
            return (0 if tier == "chirp3-hd" else 1, v)
        return sorted(set(voices), key=sort_key)
    except Exception as e:
        logger.warning(f"Google TTS list voices failed: {e}")
        return []

def _google_voice_tier(name: str) -> str | None:
    if "Chirp3-HD" in name:
        return "chirp3-hd"
    if "Standard" in name:
        return "standard"
    return None

def list_edge_tts_languages(allowed: list[str] | None = None) -> list[str]:
    langs = set()
    for v in get_all_azure_voices():
        parts = v.split("-")
        if len(parts) >= 2:
            langs.add(f"{parts[0]}-{parts[1]}")
    results = sorted(langs)
    if allowed:
        results = [lang for lang in results if lang in allowed]
    return results

def list_edge_tts_voices(language: str | None = None, allowed: list[str] | None = None) -> list[str]:
    voices = get_all_azure_voices(allowed)
    if language:
        lang = normalize_language_code(language)
        voices = [v for v in voices if v.startswith(lang)]
    return voices

def get_voice_options(allowed_langs: list[str] | None = None) -> dict:
    allowed = allowed_langs or []
    return {
        "providers": ["gtts", "edge-tts", "google-tts"],
        "languages": {
            "gtts": ["fixed-en-GB"],
            "edge-tts": list_edge_tts_languages(allowed or None),
            "google-tts": list_google_tts_languages(allowed or None),
        },
    }

def convert_rate_to_percent(rate: float) -> str:
    if rate == 1.0:
        return "+0%"
    percent = round((rate - 1.0) * 100)
    if percent > 0:
        return f"+{percent}%"
    else:
        return f"{percent}%"


async def generate_voice(
        text: str, 
        voice_name: str, 
        voice_rate: float = 0, 
        audio_file: str = None, 
        subtitle_file: str = None,
        language: str = "en-US",
        voice_provider: str = "gtts",
        lead_silence_ms: int = 0,
        trail_silence_ms: int = 0,
        sentence_pause_ms: int | None = None,
        karaoke: bool = False,
        sequence_mode: bool = False,
        ) -> Tuple[str, str]:
    """生成语音和字幕

    Args:
        text (str): 文本内容
        voice_name (str): 语音名称
        voice_rate (float, optional): 语音速率. Defaults to 0.
        audio_file (str, optional): 语音文件路径. Defaults to None.
        subtitle_file (str, optional): 字幕文件路径. Defaults to None.

    Returns:
        Tuple[str, str]: 语音文件路径, 字幕文件路径
    """
    if audio_file is None:
        audio_file = f"temp_{uuid.uuid4()}.mp3"
    if subtitle_file is None:
        subtitle_file = f"temp_{uuid.uuid4()}.srt"

    provider = (voice_provider or "gtts").lower()
    if provider in ("gtts", "google-tts", "google", "google-cloud-tts"):
        if not voice_name or voice_name == "default":
            logger.info("Using default voice")
    if provider == "edge-tts":
        voice_name = parse_voice_name(voice_name or "")
        await edge_tts_voice_notwork(text, voice_name, audio_file, subtitle_file, voice_rate, karaoke=karaoke)
    elif provider in ("google-tts", "google", "google-cloud-tts"):
        await google_cloud_tts_voice(
            text,
            audio_file,
            subtitle_file,
            language,
            voice_name,
            voice_rate,
            karaoke=karaoke,
            sentence_pause_ms=sentence_pause_ms,
            sequence_mode=sequence_mode,
        )
    else:
        await gtts_voice(
            text,
            audio_file,
            subtitle_file,
            language,
            voice_rate,
            lead_silence_ms=lead_silence_ms,
            trail_silence_ms=trail_silence_ms,
            sentence_pause_ms=sentence_pause_ms,
            karaoke=karaoke,
            sequence_mode=sequence_mode,
        )
    
    return audio_file, subtitle_file

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=3, max=30),
    retry=retry_if_exception_type((Exception,)),  # retry on all errors, including 403
    reraise=True
)
async def gtts_voice(
    text: str,
    voice_file: str,
    subtitle_file: str,
    language: str,
    voice_rate: float = 0,
    lead_silence_ms: int = 0,
    trail_silence_ms: int = 0,
    sentence_pause_ms: int | None = None,
    karaoke: bool = False,
    sequence_mode: bool = False,
):
    """
    Fallback to gTTS (Google TTS) — stable, no 403 issues.
    Note: gTTS doesn't support exact rate or SubMaker subtitles — use simple text split for .srt if needed.
    """
    
    tld = "com"  # US default
    if language in ["fixed-en-GB", "en-GB", "en-UK", "uk", "gb", "British"]:
        tld = "co.uk"  # British English
        logger.info("Using gTTS with UK English (tld=co.uk)")
    else:
        logger.info("Using gTTS with US English")

    logger.info(f"gTTS generating | text length: {len(text)} | voice: {tld} | rate approx: {voice_rate}")

    try:
        clean_text = sanitize_text_for_tts(text)
        tts_text = expand_contractions(clean_text)
        # Approximate rate: slow=True for slower speech
        slow = voice_rate < 0.8
        segment_word_timings: list[dict] = []
        if sequence_mode:
            tokens = [m.group(0) for m in KARAOKE_WORD_PATTERN.finditer(clean_text or "")]
            if not tokens:
                tokens = split_string_by_punctuations(clean_text)
            pause_ms = 120 if sentence_pause_ms is None else max(0, int(sentence_pause_ms))
            combined = AudioSegment.empty()
            cursor_ms = 0
            for idx, token in enumerate(tokens):
                seg_text = expand_contractions(sanitize_text_for_tts(token))
                if not seg_text:
                    continue
                tts = gTTS(text=seg_text, lang="en", tld=tld, slow=slow)
                buffer = io.BytesIO()
                tts.write_to_fp(buffer)
                buffer.seek(0)
                seg_audio = AudioSegment.from_file(buffer, format="mp3")
                seg_start_ms = cursor_ms
                combined += seg_audio
                cursor_ms += len(seg_audio)
                seg_end_ms = cursor_ms
                segment_word_timings.append(
                    {
                        "word": sanitize_text_for_tts(token),
                        "start": round(seg_start_ms / 1000.0, 4),
                        "end": round(max((seg_start_ms + 30) / 1000.0, seg_end_ms / 1000.0), 4),
                    }
                )
                if idx < len(tokens) - 1:
                    combined += AudioSegment.silent(duration=pause_ms)
                    cursor_ms += pause_ms
            combined.export(voice_file, format="mp3")
        else:
            segments = split_string_by_punctuations(clean_text)
            if len(segments) <= 1:
                tts = gTTS(text=tts_text, lang="en", tld=tld, slow=slow)
                tts.save(voice_file)
            else:
                pause_ms = 220 if sentence_pause_ms is None else max(0, int(sentence_pause_ms))
                combined = AudioSegment.empty()
                cursor_ms = 0
                for idx, segment in enumerate(segments):
                    seg_text = expand_contractions(sanitize_text_for_tts(segment))
                    tts = gTTS(text=seg_text, lang="en", tld=tld, slow=slow)
                    buffer = io.BytesIO()
                    tts.write_to_fp(buffer)
                    buffer.seek(0)
                    seg_audio = AudioSegment.from_file(buffer, format="mp3")
                    combined += seg_audio
                    cursor_ms += len(seg_audio)
                    if idx < len(segments) - 1:
                        combined += AudioSegment.silent(duration=pause_ms)
                        cursor_ms += pause_ms
                combined.export(voice_file, format="mp3")

        logger.info(f"gTTS success → saved: {voice_file}")

        # Get real audio duration
        audio = AudioSegment.from_mp3(voice_file)
        lead_shift_s = max(0.0, float(lead_silence_ms or 0) / 1000.0)
        if lead_silence_ms or trail_silence_ms:
            audio = AudioSegment.silent(duration=int(lead_silence_ms)) + audio + AudioSegment.silent(duration=int(trail_silence_ms))
            audio.export(voice_file, format="mp3")

        if sequence_mode and segment_word_timings:
            words: list[dict] = []
            for idx, item in enumerate(segment_word_timings):
                start_s = float(item["start"]) + lead_shift_s
                end_s = float(item["end"]) + lead_shift_s
                if idx + 1 < len(segment_word_timings):
                    next_start_s = float(segment_word_timings[idx + 1]["start"]) + lead_shift_s
                    end_s = max(end_s, next_start_s - 0.01)
                words.append(
                    {
                        "word": item["word"],
                        "start": round(max(0.0, start_s), 4),
                        "end": round(max(start_s + 0.03, end_s), 4),
                    }
                )
            build_srt_from_word_timings(words, subtitle_file)
            if karaoke or sequence_mode:
                _write_karaoke_words_file(
                    subtitle_file,
                    "gtts",
                    words,
                    timing_source="token_synth",
                    timing_quality="precise",
                )
            return

        build_srt_from_audio(audio, clean_text, subtitle_file)
        if karaoke:
            words = _karaoke_words_from_subtitle(subtitle_file)
            _write_karaoke_words_file(
                subtitle_file,
                "gtts",
                words,
                timing_source="subtitle_derived",
                timing_quality="approx",
            )
    except Exception as e:
        logger.error(f"gTTS failed: {str(e)}")
        raise

async def google_cloud_tts_voice(
    text: str,
    voice_file: str,
    subtitle_file: str,
    language: str,
    voice_name: str | None = None,
    voice_rate: float = 1.0,
    karaoke: bool = False,
    sentence_pause_ms: int | None = None,
    sequence_mode: bool = False,
):
    clean_text = sanitize_text_for_tts(text)
    lang = normalize_language_code(language) or "en-GB"
    if not voice_name:
        candidates = list_google_tts_voices(lang)
        voice_name = candidates[0] if candidates else None
    if not voice_name:
        raise RuntimeError(f"No Google TTS voices available for language {lang}")
    logger.info(f"Google TTS | language={lang} | voice={voice_name} | rate={voice_rate}")
    client = texttospeech.TextToSpeechClient()
    voice = texttospeech.VoiceSelectionParams(language_code=lang, name=voice_name)
    speaking_rate = min(4.0, max(0.25, float(voice_rate or 1.0)))
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
    )

    if sequence_mode:
        tokens = [m.group(0) for m in KARAOKE_WORD_PATTERN.finditer(clean_text or "")]
        if not tokens:
            tokens = split_string_by_punctuations(clean_text)
        pause_ms = 120 if sentence_pause_ms is None else max(0, int(sentence_pause_ms))
        combined = AudioSegment.empty()
        cursor_ms = 0
        words: list[dict] = []
        for idx, token in enumerate(tokens):
            token_text = sanitize_text_for_tts(token)
            if not token_text:
                continue
            response = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=token_text),
                voice=voice,
                audio_config=audio_config,
            )
            seg_audio = AudioSegment.from_file(io.BytesIO(response.audio_content), format="mp3")
            seg_start_ms = cursor_ms
            combined += seg_audio
            cursor_ms += len(seg_audio)
            seg_end_ms = cursor_ms
            words.append(
                {
                    "word": token_text,
                    "start": round(seg_start_ms / 1000.0, 4),
                    "end": round(max((seg_start_ms + 30) / 1000.0, seg_end_ms / 1000.0), 4),
                }
            )
            if idx < len(tokens) - 1:
                combined += AudioSegment.silent(duration=pause_ms)
                cursor_ms += pause_ms
        combined.export(voice_file, format="mp3")
        build_srt_from_lines_and_words(clean_text, words, subtitle_file)
        if karaoke or sequence_mode:
            _write_karaoke_words_file(
                subtitle_file,
                "google-tts",
                words,
                timing_source="token_synth",
                timing_quality="precise",
            )
        return

    # --- Single synthesis for natural prosody ---
    synthesis_input = texttospeech.SynthesisInput(text=clean_text)
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )
    with open(voice_file, "wb") as out:
        out.write(response.audio_content)

    # --- Non-karaoke: build SRT from audio silence detection and return ---
    if not karaoke:
        audio = AudioSegment.from_mp3(voice_file)
        build_srt_from_audio(audio, clean_text, subtitle_file)
        return

    # --- Karaoke: try Whisper forced alignment first (best quality) ---
    logger.info("Google TTS: attempting Whisper forced alignment for karaoke timing")
    whisper_words = _whisper_align_words(voice_file, clean_text)

    if whisper_words:
        # Whisper success — build precise SRT and karaoke file
        build_srt_from_lines_and_words(clean_text, whisper_words, subtitle_file)
        _write_karaoke_words_file(
            subtitle_file,
            "google-tts",
            whisper_words,
            timing_source="whisper_align",
            timing_quality="precise",
        )
        logger.info(f"Karaoke timing: whisper_align, {len(whisper_words)} words")
        return

    # --- Fallback: per-word synthesis (slower but always precise) ---
    logger.warning("Whisper unavailable, falling back to per-word synthesis for karaoke timing")
    word_tokens = [m.group(0) for m in KARAOKE_WORD_PATTERN.finditer(clean_text or "")]
    if not word_tokens:
        word_tokens = split_string_by_punctuations(clean_text)
    inter_word_pause_ms = 80
    combined = AudioSegment.empty()
    cursor_ms = 0
    words_fallback: list[dict] = []
    for w_idx, token in enumerate(word_tokens):
        token_clean = sanitize_text_for_tts(token)
        if not token_clean:
            continue
        try:
            resp = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=token_clean),
                voice=voice,
                audio_config=audio_config,
            )
            seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
        except Exception as e:
            logger.warning(f"per-word TTS failed for '{token}': {e}")
            seg = AudioSegment.silent(duration=200)
        seg_start_ms = cursor_ms
        combined += seg
        cursor_ms += len(seg)
        seg_end_ms = cursor_ms
        words_fallback.append({
            "word": token,
            "start": round(seg_start_ms / 1000.0, 4),
            "end": round(max((seg_start_ms + 30) / 1000.0, seg_end_ms / 1000.0), 4),
        })
        if w_idx < len(word_tokens) - 1:
            combined += AudioSegment.silent(duration=inter_word_pause_ms)
            cursor_ms += inter_word_pause_ms
    combined.export(voice_file, format="mp3")
    build_srt_from_lines_and_words(clean_text, words_fallback, subtitle_file)
    _write_karaoke_words_file(
        subtitle_file,
        "google-tts",
        words_fallback,
        timing_source="per_word_synth",
        timing_quality="precise",
    )




async def edge_tts_voice_notwork(
    text: str,
    voice_name: str,
    voice_file: str,
    subtitle_file: str,
    voice_rate: float = 0,
    karaoke: bool = False,
):
    """使用 Edge TTS 生成语音"""
    rate_str = convert_rate_to_percent(voice_rate)

    # Rotate a realistic browser User-Agent each attempt
    custom_ua = ua.random
    logger.info(f"edge-tts attempt | UA: {custom_ua} | voice: {voice_name}")
        
    try:
        # Monkey-patch edge_tts's internal websocket to use custom UA
        original_connect = websockets.connect
        async def patched_connect(*args, **kwargs):
            kwargs.setdefault("extra_headers", {})
            kwargs["extra_headers"]["User-Agent"] = custom_ua
            return await original_connect(*args, **kwargs)
        # Temporarily patch
        websockets.connect = patched_connect

        communicate = edge_tts.Communicate(text, voice_name, rate=rate_str)
        sub_maker = edge_tts.SubMaker()
        
        karaoke_words: list[dict] = []
        with open(voice_file, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    logger.debug(f"Got word boundary: {chunk}")
                    # 使用 SubMaker 的 create_sub 方法创建字幕
                    sub_maker.create_sub((chunk["offset"], chunk["duration"]), chunk["text"])
                    if karaoke:
                        raw_word = (chunk.get("text") or "").strip()
                        word = sanitize_text_for_tts(raw_word)
                        if word:
                            start_s = float(chunk.get("offset", 0)) / 10000000.0
                            duration_s = float(chunk.get("duration", 0)) / 10000000.0
                            end_s = start_s + max(0.03, duration_s)
                            karaoke_words.append(
                                {
                                    "word": word,
                                    "start": round(max(0.0, start_s), 4),
                                    "end": round(max(start_s + 0.03, end_s), 4),
                                }
                            )

        if not sub_maker or not sub_maker.subs:
            raise RuntimeError("No subtitles generated")

        logger.info(f"completed, output file: {voice_file}")

        # 生成字幕
        if sub_maker:
            await generate_subtitle(sub_maker, text, subtitle_file)
            if karaoke:
                timing_source = "word_boundary"
                timing_quality = "precise"
                if not karaoke_words:
                    karaoke_words = _karaoke_words_from_subtitle(subtitle_file)
                    timing_source = "subtitle_derived"
                    timing_quality = "approx"
                _write_karaoke_words_file(
                    subtitle_file,
                    "edge-tts",
                    karaoke_words,
                    timing_source=timing_source,
                    timing_quality=timing_quality,
                )
        else:
            logger.error("Failed to generate sub_maker")
    except Exception as e:
        logger.error(f"failed, error: {str(e)}")
        raise  # tenacity retries
    finally:
        # Restore original websocket connect
        websockets.connect = original_connect


async def generate_subtitle(sub_maker: edge_tts.SubMaker, text: str, subtitle_file: str):
    """生成字幕文件"""
    try:
        if not sub_maker or not hasattr(sub_maker, "subs") or not sub_maker.subs:
            print("No subtitles to generate: sub_maker is None or sub_maker.subs is empty")
            return

        print(f"Generating subtitles with {len(sub_maker.subs)} items")
        
        # 直接使用创建字幕的函数
        await create_subtitle(sub_maker=sub_maker, text=text, subtitle_file=subtitle_file)
            
    except Exception as e:
        print(f"failed to generate subtitle: {str(e)}")
        import traceback
        print(traceback.format_exc())


def get_audio_duration(sub_maker: edge_tts.SubMaker) -> float:
    """获取音频时长（秒）"""
    if not sub_maker or not hasattr(sub_maker, "subs") or not sub_maker.subs:
        return 0
    last_sub = sub_maker.subs[-1]
    start, duration = last_sub[0]
    return (start + duration) / 10000000  # 转换为秒


def _format_text(text: str) -> str:
    text = text.replace("[", " ")
    text = text.replace("]", " ")
    text = text.replace("(", " ")
    text = text.replace(")", " ")
    text = text.replace("{", " ")
    text = text.replace("}", " ")
    text = text.strip()
    return text




async def create_subtitle(sub_maker: edge_tts.SubMaker, text: str, subtitle_file: str):
    """
    优化字幕文件
    1. 将字幕文件按照标点符号分割成多行
    2. 逐行匹配字幕文件中的文本
    3. 生成新的字幕文件
    """
    text = _format_text(text)

    def formatter(idx: int, start_time: float, end_time: float, sub_text: str) -> str:
        """
        1
        00:00:00,000 --> 00:00:02,360
        跑步是一项简单易行的运动
        """
        start_t = mktimestamp(start_time).replace(".", ",")
        end_t = mktimestamp(end_time).replace(".", ",")
        return f"{idx}\n{start_t} --> {end_t}\n{sub_text}\n"

    start_time = -1.0
    sub_items = []
    sub_index = 0

    script_lines = split_text_for_subtitles(text)
    logger.debug(f"Split text into {len(script_lines)} lines: {script_lines}")

    def match_line(_sub_line: str, _sub_index: int):
        if len(script_lines) <= _sub_index:
            return ""

        _line = script_lines[_sub_index]
        if _sub_line == _line:
            return script_lines[_sub_index].strip()

        _sub_line_ = re.sub(r"[^\w\s]", "", _sub_line)
        _line_ = re.sub(r"[^\w\s]", "", _line)
        if _sub_line_ == _line_:
            return _line_.strip()

        _sub_line_ = re.sub(r"\W+", "", _sub_line)
        _line_ = re.sub(r"\W+", "", _line)
        if _sub_line_ == _line_:
            return _line.strip()

        return ""

    sub_line = ""

    try:
        for _, (offset, sub) in enumerate(zip(sub_maker.offset, sub_maker.subs)):
            _start_time, end_time = offset
            if start_time < 0:
                start_time = _start_time

            sub = unescape(sub)
            sub_line += sub
            sub_text = match_line(sub_line, sub_index)
            if sub_text:
                sub_index += 1
                line = formatter(
                    idx=sub_index,
                    start_time=start_time,
                    end_time=end_time,
                    sub_text=sub_text,
                )
                sub_items.append(line)
                start_time = -1.0
                sub_line = ""
        if len(sub_items) == len(script_lines):
            with open(subtitle_file, "w", encoding="utf-8") as file:
                file.write("\n".join(sub_items) + "\n")
            try:
                sbs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
                duration = max([tb for ((ta, tb), txt) in sbs])
                logger.info(
                    f"completed, subtitle file created: {subtitle_file}, duration: {duration}"
                )
            except Exception as e:
                logger.error(f"failed, error: {str(e)}")
                os.remove(subtitle_file)
        else:
            logger.error(
                f"failed, sub_items len: {len(sub_items)}, script_lines len: {len(script_lines)}"
            )

    except Exception as e:
        logger.error(f"failed, error: {str(e)}")
