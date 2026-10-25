# 剪映内置 TTS：一条字幕，一段独立音频

用于二创旁白和最终需要在剪映逐句调整的项目。先固定字幕单位，再分别生成；不要生成整段音频后切片，也不要用一条合成总音轨替代工程里的独立句素材。

## 音色与入口

- 使用 `jianying-editor` 的 Python 与 `run_skill.py`。脚本和产物留在用户项目，工具维护代码可放在本 skill 的 `helpers/`。
- 按用户确认的剪映音色名称核对模型。旧 CSV 找不到时，可只读查剪映 `Cache/ressdk_db/*/rp.db` 的公开素材 `http_cache.response_body`，精确匹配 `title`，再解析 `extra.tonetype.voice_type`。只保存名称、资源 ID、模型 ID 和版本，不输出账号请求参数、签名或凭据。
- 已实测音色：
  - **台湾腔甜妹**（女声默认）：TTS 模型 `zh_female_xiaohe_saturn_bigtts`；资源 `7553535833219812618`；配置版本 v3。同名变声项 `VC_zh_female_xiaohe` 属于 voice conversion，不能拿来合成旁白。
  - **气泡音小千**（男声默认）：TTS 模型 `saturn_zh_male_qianxixiaobei`；资源 `7600680247834578186`；VIP 必需。
- 强制 `backend="sami", allow_fallback=False`。服务失败、音色不支持或会员权限不足时停止并保留已成功句，说明真实错误；不替换声音，不调用 Edge 兜底。音色或会员资格的缓存信息不代表实时授权，以生成结果为准。

## 字幕与独立音频

修改字幕时区分"只改显示"和"需要重配"：

- 修正错字、显示标点等且读音和意思不变时，只改对应字幕文本，不重新生成音频。
- 改动朗读措辞、语气、数字或专名读音时，重配受影响的句子。
- 断句拆分或合并时重新建立受影响的字幕／音频映射；保留其余 cue 的稳定 ID，不重新合成整段。
- 换入音频后按新音频实际时长更新对应字幕和必要的后续时间，检查旁白、原声及留白衔接；同步工程与导出的 SRT。不能只更新 Markdown 或字幕文件，却留下旧工程台词或旧声音。

1. 保留用户当前修改稿，审核回执通过后才生成或复用缓存，单句试配也不能绕开审核。
2. 只提取显式 `**旁白**` 块，原声译文和留白指令不送 TTS。字幕按完整语义短句定稿，**单条字幕不超过 25 个字符**；逗号、句号等可作候选边界，不在专名或词中间硬切。超长子句优先在顿号 `、`、自然动词短语或介词处二次拆分。
3. 每条最终字幕一个稳定 `cue_id`，独立发送一次文本，保存独立 WAV。音频缓存键包含模型和朗读文字的哈希；改一句只重生成这句。不要把段落先合成后分割。
4. WAV 必须有有效音频流、非零时长。保存字幕、朗读文字、路径、音色、后端、哈希及解码时长。剪映导入时以 AudioSegment 实际时长更新字幕，并按上一段实际终点接下一句；容器时长的亚毫秒舍入不得造成重叠或额外音轨。
5. 不自动增加旧版 +15% 语速。按原生声音试配和实际试听决定后续调整；调整时将速度写入配置和缓存键，重算时长。

## 已验证命令

```powershell
& 'C:/Users/yinsh/.workbuddy/skills/jianying-editor/.venv/Scripts/python.exe' -X utf8 `
  'C:/Users/yinsh/.workbuddy/skills/jianying-editor/run_skill.py' `
  'C:/Users/yinsh/.workbuddy/skills/video-use/helpers/jianying_tts.py' `
  --from-script '<项目>/gameshow-recap-creator/recap-script.md' `
  -o '<项目>/audio/jianying-sentences' --limit 3
```

试配确认生成成功、音色 ID 正确、字幕和独立文件对应后，去掉 `--limit 3` 生成全稿。`--plan-only` 只输出字幕计划，不发文本到语音服务。

单句重生成可用 `--cue-id s003_c002 --force`。它只生成该句并写独立的 `regeneration-<cue_id>.json`，不会覆盖整稿时间线；改过脚本仍先更新整稿审核。换入剪映对应音频后，重新检查该句字幕及后续时间，不能把旧主字幕时间轴当成仍然有效。

## 产物与剪映工程

- `sentences/*.wav`：每条字幕独立生成的声音。
- `sentences-manifest.json`：整稿字幕与音频一对一映射、音色、哈希、实测时长。试配有明确 `pilot` 状态，不能当整稿完成。
- `captions-plan.json`、`captions-review.tsv`：最终字幕单位与对应声音，可逐句核对。
- `narration-only.srt`：仅用于配音审阅，不是带原声与留白的最终成片字幕。

导入工程时，每句创建独立 AudioMaterial/AudioSegment 和 TextSegment，时间对齐；将素材复制到草稿内以防外部文件丢失。旁白、原声交流和无人声制作留白分别安排，原声字幕不生成旁白。按最终剪辑重建 `master.srt` 与渲染兼容的 slot manifest；需要的总旁白文件只是派生试听／渲染文件。

交付前验证：独立音频文件数＝旁白字幕 cue 数＝旁白音频 segment 数；逐项路径存在、音色／后端一致、字幕起止等于对应音频，且每个素材不是同一个长文件的不同裁切。原声译文不计入这个旁白数量。
