"""Narrow existing-subject profile synchronization; no account linking or role writes."""

import os

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def enabled(provider):
    return provider == 'oidc' and os.environ.get('MAIN_OAUTH_PROFILE_SYNC_ENABLED') == 'true'


async def sync_profile(user, user_data, auth_config, users, db=None):
    changes = {}
    if auth_config.OAUTH_UPDATE_NAME_ON_LOGIN:
        name = user_data.get(auth_config.OAUTH_USERNAME_CLAIM)
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise HTTPException(409, detail='主站用户名不可同步，请联系管理员。')
        if name != user.name:
            changes['name'] = name
    if auth_config.OAUTH_UPDATE_EMAIL_ON_LOGIN:
        email = user_data.get(auth_config.OAUTH_EMAIL_CLAIM)
        if user_data.get('email_verified') is not True or not isinstance(email, str) or not email:
            raise HTTPException(409, detail='请先在主站验证邮箱，再重新登录。')
        email = email.lower()
        if email != user.email.lower():
            owner = await users.get_user_by_email(email, db=db)
            if owner is not None and owner.id != user.id:
                raise HTTPException(409, detail='主站资料与已有账号冲突，请联系管理员。')
            changes['email'] = email
    if not changes:
        return user, []
    try:
        # Pinned Users.update_user_by_id writes User and Auth email in one commit.
        # Passing only these columns cannot change role, password or OAuth subject.
        updated = await users.update_user_by_id(user.id, changes, db=db)
        if updated is None:
            raise HTTPException(409, detail='主站资料同步失败，请重新登录或联系管理员。')
    except IntegrityError:
        if db is not None:
            await db.rollback()
        # Do not log exception text: database errors can contain the full email.
        raise HTTPException(409, detail='主站资料与已有账号冲突，请联系管理员。') from None
    except HTTPException:
        raise
    except Exception:
        if db is not None:
            await db.rollback()
        raise HTTPException(409, detail='主站资料同步失败，请重新登录或联系管理员。') from None
    return updated, list(changes)
