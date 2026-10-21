"""Best-effort local macOS notifications; strings are data, never AppleScript source."""
import logging
import subprocess
import sys


SCRIPT = '''on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
'''


def notify_macos(title: str, message: str) -> bool:
    if sys.platform != 'darwin':
        return False
    try:
        result = subprocess.run(['/usr/bin/osascript', '-', title, message], input=SCRIPT,
                                text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=3, check=False)
        if result.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    logging.warning('macOS 알림을 보내지 못했습니다.')
    return False
