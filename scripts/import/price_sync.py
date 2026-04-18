# -*- coding: utf-8 -*-
"""
CLI: синхронизация цен с opt-akvabreg.by.
Конфиг логина — .opt_config.json в корне проекта (рядом с estimate_module.py),
тот же файл, что заполняет веб-интерфейс «Синхронизация цен».
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from price_sync import main  # noqa: E402

if __name__ == '__main__':
    main()
