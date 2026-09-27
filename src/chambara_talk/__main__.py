"""``python -m chambara_talk``で起動されたときの入口。"""

from .cli import main


# main()が返す終了コードをOSへ渡します。
raise SystemExit(main())
