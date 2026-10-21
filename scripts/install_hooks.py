"""Enable repository hooks without replacing an existing hook configuration."""
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        git_root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root,
                                               stderr=subprocess.DEVNULL).decode().strip()).resolve()
        if git_root != root:
            raise ValueError('이 프로젝트의 Git 저장소에서 실행하세요.')
        current = subprocess.run(['git', 'config', '--get', 'core.hooksPath'], cwd=root,
                                 capture_output=True, text=True)
        if current.returncode not in (0, 1):
            raise ValueError('기존 Git hook 설정을 확인할 수 없습니다.')
        if current.returncode == 0 and current.stdout.strip() != '.githooks':
            raise ValueError('기존 core.hooksPath 설정이 있어 변경하지 않았습니다.')
        if current.returncode == 1:
            old_hook = Path(subprocess.check_output(['git', 'rev-parse', '--git-path', 'hooks/pre-commit'],
                                                   cwd=root).decode().strip())
            if not old_hook.is_absolute():
                old_hook = root / old_hook
            if old_hook.exists():
                raise ValueError('기존 pre-commit hook이 있어 변경하지 않았습니다.')
        hook = root / '.githooks' / 'pre-commit'
        hook.chmod(hook.stat().st_mode | 0o111)
        subprocess.run(['git', 'config', '--local', 'core.hooksPath', '.githooks'], cwd=root, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'설치 실패: {error}', file=sys.stderr)
        return 1
    print('커밋 전 검사 활성화 완료 (core.hooksPath=.githooks)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
