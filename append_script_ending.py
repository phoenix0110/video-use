from pathlib import Path
import re

root = Path('C:/Users/yinsh/WorkBuddy/B站二创视频/outputs/projects/fBY0kfiENcs-china-fast-food/gameshow-recap-creator')
script = root / 'recap-script.md'
text = script.read_text(encoding='utf-8-sig')
(root / 'draft-history/recap-script.user-edit.before-ending.md').write_text(text, encoding='utf-8')
ending = '''### 6:32–6:39｜小铃铃收尾：彦祖、亦菲们安排一下

**画面**：[10:48–11:29] 从已核查的小杨生煎制作画面中选一段连续镜头，接在最终原声之后；画面长度以本句实际配音为准，不使用原片结束卡

**旁白**
> 各位彦祖、亦菲们，赶紧去美国开小杨生煎吧！

'''
assert '### 6:32–6:39' not in text
text = text.replace('## 包装清单', ending + '## 包装清单', 1)
text = text.replace('规划时长约6:32', '规划时长约6:39').replace('约占规划时长35.4%', '约占规划时长34.8%')
script.write_text(text, encoding='utf-8')

review = []
body = False
dialogue = False
chinese = False
for line in text.splitlines():
 if not body:
  if line == '## 脚本':
   body = True
  else:
   if line.startswith('> 源视频：'):
    continue
   review.append(line)
   continue
 if line == '## 包装清单':
  break
 if line.startswith('### '):
  dialogue = False
 if line == '**原声全文与中文译文**':
  review.append('**保留原声（以下为中文字幕，不另配中文音）**')
  dialogue = True
  chinese = False
 elif dialogue and line.startswith('> '):
  if chinese:
   review.append(line)
  chinese = not chinese
 elif line.startswith('**进入与退出**'):
  dialogue = False
  review.append(line)
 elif line.startswith('**待核听**'):
  review.append('**待核听**：塔斯汀末段一句译文暂按上下文处理，剪辑前核听确认。')
 else:
  review.append(line)
review += ['', '以上以用户最新修改的 recap-script.md 为准；本次新增最后的小铃铃收尾。时码仍为规划，原声后续逐段独立拆出。']
(root / 'recap-script-review.md').write_text(re.sub(r'\n{3,}', '\n\n', '\n'.join(review)) + '\n', encoding='utf-8')
print('Appended closing narration; preserved user text and synchronized Chinese review copy.')
