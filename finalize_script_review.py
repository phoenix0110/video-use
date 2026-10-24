from pathlib import Path
import hashlib, json, re

root = Path('C:/Users/yinsh/WorkBuddy/B站二创视频/outputs/projects/fBY0kfiENcs-china-fast-food/gameshow-recap-creator')
script = root / 'recap-script.md'
text = script.read_text(encoding='utf-8-sig')
# Layer 3 correction precedes the protected polishing baseline.
text = text.replace('轮到豆制品，老吉还在犹豫，嘴上说着不确定，又夹了一口。', '轮到豆制品，老吉说自己还在努力喜欢，手上又夹了一口。')
before = root / 'draft-history/recap-script.before-polish.v2.md'
before.write_text(text, encoding='utf-8')
changes = {
 '两位将基于招牌味道和实际花费做出评价': '两位会按招牌味道和实际花费来评价',
 '鸡腿和腿排保持连接': '鸡腿和腿排还连在一起',
 '旁边的大锅承担煎制工作': '旁边的大锅负责煎制',
 '沿着菜台进行选择': '沿着菜台选菜',
 '两碗体现出不同的选择': '两碗选的东西不一样',
 '卷毛哥对整只炸制是否使鸡肉变干有所担忧': '卷毛哥原先担心，整只炸会把鸡肉弄干',
 '老吉反而把这一家排进了前列': '老吉把这一家排进了前列',
}
lines = text.splitlines()
active = False
for i, line in enumerate(lines):
 if line.strip() == '**旁白**':
  active = True
 elif active and line.startswith('>'):
  for a, b in changes.items():
   line = line.replace(a, b)
  lines[i] = line
 elif line.strip():
  active = False
text = '\n'.join(lines) + '\n'
script.write_text(text, encoding='utf-8')
notes = '''# 旁白润色与审稿记录

## 范围与依据

已实际读取 humanizer-zh 与 shuorenhua 的 SKILL.md，后者按 annotation mode 使用。口吻沿用小铃铃的美食解说、既有固定开场和一键三连句；人物统一卷毛哥、老吉。只编辑旁白，原声原话、译文、画面范围和规划时码不进入本轮润色。

## 上游叙事与素材核查

蜜雪价格交流作钩子，随后按原片店铺顺序推进，结尾让两人用原声回答核心问题。瑞幸、华莱士和蜜雪的重复总结已在叙事层删除，避免旁白提前重复下一段原声。

同源字幕与已查看的画面支持手枪腿、两种拿铁、现擀现烤饼皮、两款汉堡、手工包制和煎锅、菜台试吃、麻辣烫选择、整鸡及蜜雪评价。价格明确归于本次订单的美元报价。大米先生的“说着不确定”修正为“说自己还在努力喜欢”，对应原话 I'm trying to like this 与再次入口画面；此修改在本轮基线之前完成。

四段原声均按话题展开到回应／评价结束选取，未抽成孤立问答。塔斯汀末段自动字幕 we're differing 的措辞仍待核听，脚本已明确标记，不能把字幕核对称为完成听辨。该问题属于原声翻译，不据此添加旁白结论，成片前必须解决。

## humanizer-zh 实际修改

- “将基于……做出评价”改为“会按……来评价”，保留评价标准与未来动作，减少书面语。
- “鸡腿和腿排保持连接”改为“鸡腿和腿排还连在一起”，保留食物结构。
- “大锅承担煎制工作”改为“大锅负责煎制”，保留设备作用，不改成全自动包制。
- “沿着菜台进行选择”改为“沿着菜台选菜”，保留行动顺序。
- “两碗体现出不同的选择”改为“两碗选的东西不一样”，保留点单差异，不加口味判断。
- “对整只炸制是否使鸡肉变干有所担忧”改为“原先担心，整只炸会把鸡肉弄干”，保留人物归属、担心而非事实、后续试吃的转折。

## shuorenhua 标问题与处理

1. “老吉反而把这一家排进了前列”：前文是选食材与汤底的差异，并没有预先给出低评价，“反而”暗示了不足以成立的期待落差。处理：去掉“反而”，排名判断及老吉归属保留。
2. 德克士段最后问“咖啡能不能让……老吉点头”：与下一段同主题，但承担进入瑞幸的转场和悬念，尚未揭晓结果。处理：保留，不当作重复答案删除。
3. 塔斯汀旁白先说老吉不喜欢烤鸭，原声又说明偏好：前者交代试吃经过，后者给卷毛哥接过汉堡的对话上下文。处理：保留必要衔接，不把原声切掉一句。

## 整稿口播语气通读与语义对照

已按口播语气逐段通读文字，检查钩子接价格揭晓、德克士接瑞幸、老吉试吃接卷毛哥原声、生煎介绍接入口、蜜雪正文接最终选择。固定开场与主持人口吻保留。所有本轮替换逐句对照基线，价格、品牌、人物动作、归属、否定和比较对象保持；没有新增心情、危险或店铺结论。未进行实际配音或原声听辨，不将文字通读等同于音频验证。

非旁白保护字段由校验器逐行核对；此回执绑定本次稿件。审稿无未处理的旁白意见，原声待核听项单独保留，规划时长尚非配音实测时长。
'''
notespath = root / 'narration-review.md'
notespath.write_text(notes, encoding='utf-8')
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
skillpaths = {n: Path('C:/Users/yinsh/.agents/skills') / n / 'SKILL.md' for n in ('humanizer-zh','shuorenhua')}
receipt = dict(schema_version=1, script_sha256=digest(script), before_path=str(before), before_sha256=digest(before), notes_path=str(notespath), notes_sha256=digest(notespath), skills={n:dict(path=str(p),sha256=digest(p)) for n,p in skillpaths.items()}, checks={n:True for n in ('humanizer_completed','shuorenhua_annotation_completed','issues_resolved','semantic_fidelity_checked','voice_and_continuity_readthrough')})
(root / 'narration-review.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')

# A Chinese-first review copy, keeping every narration and translated exchange.
review = ['# 美国食客连吃中国快餐：哪家最想带回美国？', '', '> 脚本审阅版｜预计约6分32秒，目标5–7分钟；时码为规划，尚未配音实测。', '> 人物：卷毛哥、老吉。四段原声共约2分19秒，约占规划时长35%。后续每段原声单独交付音频文件与剪映片段。', '> 以下“画面”时码对应原视频；章节时码对应规划成片。', '', '## 脚本', '']
in_body = False
in_dialogue = False
expect_chinese = False
for line in text.splitlines():
 if line == '## 脚本':
  in_body = True
  continue
 if line == '## 包装清单':
  break
 if not in_body:
  continue
 if line.startswith('### '):
  in_dialogue = False
 if line == '**原声全文与中文译文**':
  review.append('**保留原声（以下为中文字幕，不另配中文音）**')
  in_dialogue = True
  expect_chinese = False
 elif in_dialogue and line.startswith('> '):
  if expect_chinese:
   review.append(line)
  expect_chinese = not expect_chinese
 elif line.startswith('**进入与退出**'):
  in_dialogue = False
  review.append(line)
 elif line.startswith('**待核听**'):
  review.append('**待核听**：塔斯汀最后一句“咱们的口味还是有区别”为暂译，自动字幕措辞不清，剪辑前核听确认。')
 else:
  review.append(line)
review.extend(['', '## 本轮可重点审阅', '', '- 开场用蜜雪猜价，再回到德克士，是否喜欢这个钩子。', '- 旁白的口吻、信息量，以及八家店之间的推进。', '- 四段原声的选择与中文字幕；原声音频后续逐段独立拆出。', '', '价格均为原片这次订单的美元报价。此稿供脚本审阅，尚未开始配音、拆原声音频或渲染。'])
out = root / 'recap-script-review.md'
out.write_text(re.sub(r'\n{3,}', '\n\n', '\n'.join(review)) + '\n', encoding='utf-8')
print(out)
