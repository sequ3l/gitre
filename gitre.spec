# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for gitre standalone executable.

Usage:
    pyinstaller gitre.spec

Produces a single-file executable in dist/gitre (or dist/gitre.exe on Windows).

Note: The bundled executable still requires `git` to be installed on the
system PATH.  git-filter-repo is bundled as a Python library.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

block_cipher = None

# Collect submodules that PyInstaller may miss due to lazy/conditional imports.
hidden = (
    collect_submodules('gitre')
    + collect_submodules('claude_agent_sdk')
    + collect_submodules('mcp')
    + [
        'git_filter_repo',
        'httpx',
        'httpx_sse',
        'jsonschema',
        'pydantic_settings',
        'pyjwt',
        'jwt',
        'multipart',
        'typing_inspection',
    ]
)

# Copy .dist-info metadata for packages that verify their own installation
# at runtime via importlib.metadata.
metadata_datas = []
for pkg in [
    # Direct dependencies
    'claude-agent-sdk',
    'gitre',
    'pydantic',
    'typer',
    'git-filter-repo',
    # Transitive dependencies that commonly self-check
    'pydantic-core',
    'annotated-types',
    'click',
    'rich',
    'shellingham',
    'anyio',
    'sniffio',
    'markupsafe',
    'typing-extensions',
    # claude-agent-sdk transitive (via mcp)
    'mcp',
    'httpx',
    'httpx-sse',
    'jsonschema',
    'pydantic-settings',
    'pyjwt',
    'python-multipart',
    'typing-inspection',
]:
    try:
        metadata_datas += copy_metadata(pkg)
    except Exception:
        pass  # Package may not be installed in all build environments

a = Analysis(
    ['gitre/cli.py'],
    pathex=[],
    binaries=[],
    datas=metadata_datas
    + collect_data_files('certifi')                # CA certs for TLS (httpx/mcp)
    + collect_data_files('jsonschema')              # JSON schema specs
    + collect_data_files('jsonschema_specifications'),
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'pytest_asyncio',
        'pytest_cov',
        '_pytest',
        'ruff',
        'mypy',
        'pre_commit',
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='gitre',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
