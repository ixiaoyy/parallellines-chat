"""退役旧聊天代理，仅保留无参数的新站跳转；共享身份路径不变。"""
import re


def redirect_config(text):
    """输入完整 Nginx 文本，返回只替换旧 /chat 入口的文本；未知布局拒绝修改。"""
    upstream = '    set $librechat_upstream librechat:3080;\n'
    if text.count(upstream) != 1:
        raise ValueError('legacy_upstream_missing_or_duplicated')
    result = text.replace(upstream, '', 1)
    for location in ('location = /chat', 'location ^~ /chat/'):
        pattern = r'^    ' + re.escape(location) + r' \{\n.*?^    \}\n'
        replacement = ('    ' + location + ' {\n'
                       '        access_log off;\n'
                       '        add_header Cache-Control "no-store" always;\n'
                       '        return 303 https://chat.pingxingxian.space/;\n'
                       '    }\n')
        result, count = re.subn(pattern, replacement, result, count=1, flags=re.M | re.S)
        if count != 1:
            raise ValueError('legacy_chat_location_changed')
    if '$librechat_upstream' in result:
        raise ValueError('remaining_legacy_proxy_reference')
    return result
