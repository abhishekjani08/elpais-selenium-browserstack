"""
Quick standalone check for the translation API BEFORE running the full flow.
Run:  python test_translate.py
If this prints an English sentence, your RapidAPI key + host + subscription are good.
If it errors, paste the printed status/raw response and the parsing can be fixed fast.
"""

from dotenv import load_dotenv
from pipeline import translate_to_english

load_dotenv()

if __name__ == "__main__":
    sample = "La economia espanola crece mas de lo esperado este ano"
    print("ES:", sample)
    print("EN:", translate_to_english(sample, debug=True))
