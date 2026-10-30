"""Apply the minimal profile callback change to the pinned official source only."""
import hashlib
from pathlib import Path
import sys

OAUTH_SHA256 = 'b67a692507611c9afb64136597a26b1a6a157e46a4a0e386f8c7f61308bfd7d2'
USERS_SHA256 = '143e949b172252c45ae1cbe7d5f7bfc3460375fa49b203f4dbc36c6128d68bc3'


def patch(source):
    marker = 'from open_webui.models.users import Users\n'
    assert source.count(marker) == 1, 'unexpected OAuth imports'
    source = source.replace(marker, marker + 'from open_webui.utils.main_profile_sync import enabled as main_profile_sync_enabled, check_configuration as check_main_profile_configuration, sync_profile\n')
    marker = '            user = await Users.get_user_by_oauth_sub(provider, sub, db=db)\n'
    assert source.count(marker) == 1, 'unexpected OAuth subject lookup'
    source = source.replace(marker,
        '            if main_profile_sync_enabled(provider):\n'
        '                check_main_profile_configuration(auth_config)\n'
        + marker + '            main_profile_bound = user is not None and main_profile_sync_enabled(provider)\n')
    start = source.index('                if auth_config.OAUTH_UPDATE_NAME_ON_LOGIN:\n', source.index('async def handle_callback'))
    end = source.index('                # Update profile picture if enabled', start)
    original = source[start:end]
    wrapped = (
        '                if main_profile_bound:\n'
        '                    user, updated_fields = await sync_profile(user, user_data, auth_config, Users, db=db)\n'
        '                else:\n'
        + ''.join('    ' + line if line.strip() else line for line in original.splitlines(keepends=True))
        + '\n'
    )
    return source[:start] + wrapped + source[end:]


if __name__ == '__main__':
    root = Path(sys.argv[1])
    oauth = root / 'utils/oauth.py'
    users = root / 'models/users.py'
    if hashlib.sha256(oauth.read_bytes()).hexdigest() != OAUTH_SHA256:
        raise SystemExit('OAuth source differs from pinned upstream; refusing patch')
    if hashlib.sha256(users.read_bytes()).hexdigest() != USERS_SHA256:
        raise SystemExit('Users transaction differs from pinned upstream; refusing patch')
    oauth.write_text(patch(oauth.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
