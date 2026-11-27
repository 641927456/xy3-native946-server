"""Bounded data-only reader for native serialize() table payloads."""
import re

def table(text, *, limit=24000, nodes=512):
    if not isinstance(text,str) or len(text.encode('gb18030'))>limit:raise ValueError('提交的数据过长。')
    at=0;used=0
    def skip():
        nonlocal at
        while at<len(text) and text[at].isspace():at+=1
    def take(s):
        nonlocal at
        skip()
        if not text.startswith(s,at):raise ValueError('数据格式无效。')
        at+=len(s)
    def value(depth=0):
        nonlocal at,used
        skip();used+=1
        if used>nodes or depth>6 or at>=len(text):raise ValueError('数据结构过长或层级过深。')
        ch=text[at]
        if ch in ('"',"'"):
            at+=1;out=[]
            while at<len(text):
                c=text[at];at+=1
                if c==ch:
                    try:return b''.join(x if isinstance(x,bytes) else x.encode('gb18030') for x in out).decode('gb18030')
                    except (UnicodeDecodeError,UnicodeEncodeError):raise ValueError('字符串编码无效。') from None
                if c=='\\':
                    if at>=len(text):break
                    c=text[at];at+=1
                    if c.isascii() and c.isdigit():
                        digits=c
                        while at<len(text) and len(digits)<3 and text[at].isascii() and text[at].isdigit():digits+=text[at];at+=1
                        n=int(digits)
                        if n>255:raise ValueError('字符串转义无效。')
                        # serialize emits GBK bytes when it escapes non-ASCII.
                        out.append(bytes([n]))
                    else:out.append({'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','a':'\a'}.get(c,c))
                else:out.append(c)
            raise ValueError('字符串未结束。')
        if ch=='{':
            at+=1;out={};index=1
            while True:
                skip()
                if at<len(text) and text[at]=='}':at+=1;return out
                key=None
                if at<len(text) and text[at]=='[':
                    at+=1;key=value(depth+1);take(']');take('=')
                else:
                    m=re.match(r'[A-Za-z_][A-Za-z_0-9]*\s*=',text[at:])
                    if m:
                        key=m.group().split('=')[0].strip();at+=len(m.group())
                if key is None:key=index;index+=1
                if type(key) not in (int,str) or key in out:raise ValueError('数据索引重复或无效。')
                out[key]=value(depth+1);skip()
                if at<len(text) and text[at] in ',;':at+=1
                elif at>=len(text) or text[at]!='}':raise ValueError('数据分隔符无效。')
        m=re.match(r'-?\d{1,12}(?![\w.])',text[at:])
        if m:at+=len(m.group());return int(m.group())
        for word,v in (('true',True),('false',False),('nil',None)):
            if text.startswith(word,at) and (at+len(word)==len(text) or not (text[at+len(word)].isalnum() or text[at+len(word)]=='_')):
                at+=len(word);return v
        raise ValueError('只接受数据表，不接受代码或表达式。')
    skip()
    if text.startswith('return ',at):at+=7
    result=value();skip()
    if at!=len(text) or not isinstance(result,dict):raise ValueError('数据表包含多余内容。')
    return result
