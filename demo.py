"""Offline interview demo. Run from any working directory with Python 3.10+."""

from pathlib import Path
import sys

# demo.py の場所を基準に、売買デモ本体のフォルダを Python の検索対象へ追加する。
sys.path.insert(0, str(Path(__file__).resolve().parent / "etf_mobile"))

# 実際の処理は interview_demo.py にまとめ、このファイルは起動の入口だけを担当する。
from interview_demo import main


# 直接 python demo.py と実行されたときだけ起動し、戻り値を終了コードにする。
if __name__ == "__main__":
    raise SystemExit(main())
