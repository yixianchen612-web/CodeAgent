"""CodeAgent 桌面版入口（PyInstaller 打包目标）。

双击 / 直接运行: python main.py
CLI 模式请用: python -m codeagent --help
"""

import sys

from codeagent.gui import main

if __name__ == "__main__":
    if "--offline-self-test" in sys.argv[1:]:
        from codeagent.offline_self_test import main as offline_main

        raise SystemExit(offline_main(sys.argv[1:]))
    main()
