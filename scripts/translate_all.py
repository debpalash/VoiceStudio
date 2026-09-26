import os
import json
import re
import time
from deep_translator import GoogleTranslator

LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "electron", "src", "shared", "i18n", "locales"
)

LANGUAGES = [
    "zh-CN", "es", "fr", "de", "ja", "pt", "it", "ru", "ko",
    "hi", "tr", "pl", "nl", "sv", "th", "vi", "id", "uk", "ar", "zh-TW"
]

VAR_PAT = re.compile(r'\{\{[a-zA-Z0-9_]+\}\}')
TAG_PAT = re.compile(r'<\/?[0-9]+>')
DELIM_SPLIT = re.compile(r'\s*xxx\s*', re.IGNORECASE)

def mask_text(text):
    """Mask i18next interpolations {{var}} and HTML tags <1>...</1>."""
    vars_found = VAR_PAT.findall(text)
    tags_found = TAG_PAT.findall(text)
    
    masked = text
    for idx, var in enumerate(vars_found):
        masked = masked.replace(var, f"__V_{idx}__")
    for idx, tag in enumerate(tags_found):
        masked = masked.replace(tag, f"__T_{idx}__")
        
    return masked, vars_found, tags_found

def unmask_text(translated_text, vars_found, tags_found):
    """Restore i18next interpolations and HTML tags from masks."""
    if not translated_text:
        return ""
    
    unmasked = translated_text
    
    # Restore variables
    for idx, var in enumerate(vars_found):
        unmasked = unmasked.replace(f"__V_{idx}__", var)
        # Google Translate sometimes injects spaces around underscores/indices
        unmasked = re.sub(rf'__\s*V\s*_\s*{idx}\s*__', var, unmasked)
        
    # Restore tags
    for idx, tag in enumerate(tags_found):
        unmasked = unmasked.replace(f"__T_{idx}__", tag)
        unmasked = re.sub(rf'__\s*T\s*_\s*{idx}\s*__', tag, unmasked)
        
    return unmasked

def get_flat_keys(d, prefix=""):
    """Get all leaf paths and their values from a nested dictionary."""
    flat = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(get_flat_keys(v, full_key))
        else:
            flat[full_key] = v
    return flat

def set_nested_value(d, key_path, value):
    """Set a value in a nested dictionary given a dot-separated path."""
    parts = key_path.split(".")
    curr = d
    for p in parts[:-1]:
        if p not in curr:
            curr[p] = {}
        curr = curr[p]
    curr[parts[-1]] = value

def main():
    # Load English base translations
    en_path = os.path.join(LOCALES_DIR, "en.json")
    with open(en_path, "r", encoding="utf-8") as f:
        en_data = json.load(f)
        
    en_flat = get_flat_keys(en_data)
    print(f"Loaded English base translations. Total keys: {len(en_flat)}", flush=True)
    
    for lang in LANGUAGES:
        if lang == "en":
            continue
            
        lang_path = os.path.join(LOCALES_DIR, f"{lang}.json")
        
        # Load existing target file or initialize
        if os.path.exists(lang_path):
            with open(lang_path, "r", encoding="utf-8") as f:
                try:
                    lang_data = json.load(f)
                except Exception:
                    lang_data = {}
        else:
            lang_data = {}
            
        lang_flat = get_flat_keys(lang_data)
        
        # Identify missing keys
        missing_keys = []
        for k in en_flat:
            if k not in lang_flat:
                missing_keys.append(k)
                
        print(f"\nLanguage: {lang} | Existing: {len(lang_flat)} keys | Missing: {len(missing_keys)} keys", flush=True)
        
        if not missing_keys:
            print(f"Language {lang} is already 100% complete!", flush=True)
            continue
            
        # Translate missing keys in batches of 40 using the join/split method
        batch_size = 40
        translator = GoogleTranslator(source='en', target=lang)
        
        for i in range(0, len(missing_keys), batch_size):
            batch_keys = missing_keys[i:i+batch_size]
            batch_values = [en_flat[k] for k in batch_keys]
            
            # Mask placeholders
            masked_values = []
            masks_metadata = []
            for val in batch_values:
                masked, vars_found, tags_found = mask_text(val)
                masked_values.append(masked)
                masks_metadata.append((vars_found, tags_found))
                
            print(f"  Translating batch {i//batch_size + 1}/{ -(-len(missing_keys)//batch_size) } ({len(batch_keys)} keys)...", flush=True)
            
            # Join with XXX delimiter
            joined_string = "\n\nXXX\n\n".join(masked_values)
            
            # Perform translation
            success = False
            try:
                translated_joined = translator.translate(joined_string)
                parts = [p.strip() for p in DELIM_SPLIT.split(translated_joined) if p.strip()]
                
                # Check if the split parts match the batch keys length
                if len(parts) == len(batch_keys):
                    for k, translated_val, (vars_found, tags_found) in zip(batch_keys, parts, masks_metadata):
                        final_val = unmask_text(translated_val, vars_found, tags_found)
                        set_nested_value(lang_data, k, final_val)
                    success = True
                else:
                    print(f"    Batch split length mismatch (expected {len(batch_keys)}, got {len(parts)}). Falling back to individual translation...", flush=True)
            except Exception as e:
                print(f"    Error translating batch: {e}. Falling back to individual translation...", flush=True)
                
            if not success:
                # Fallback to translating one-by-one for this batch
                for k, val, (vars_found, tags_found) in zip(batch_keys, batch_values, masks_metadata):
                    try:
                        masked, v_f, t_f = mask_text(val)
                        trans_val = translator.translate(masked)
                        final_val = unmask_text(trans_val, v_f, t_f)
                        set_nested_value(lang_data, k, final_val)
                        time.sleep(0.05)
                    except Exception as ie:
                        print(f"      Individual error for key {k}: {ie}", flush=True)
                        set_nested_value(lang_data, k, val) # Fallback to original English
                        
            time.sleep(0.2) # Sleep to be polite to the translation endpoints
            
        # Write fully updated target file
        with open(lang_path, "w", encoding="utf-8") as f:
            json.dump(lang_data, f, ensure_ascii=False, indent=2)
            
        print(f"Finished {lang}.json. Saved successfully!", flush=True)

if __name__ == "__main__":
    main()
