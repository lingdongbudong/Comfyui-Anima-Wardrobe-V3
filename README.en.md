# Comfyui-Anima-Wardrobe-V3

<p align="right">
  <a href="README.md">中文</a> ｜ <b>English</b>
</p>

> [!CAUTION]
> **⛔ Commercial use prohibited · No repackaging or redistribution** — Copyright © 2026 灵冻不冻 (lingdongbudong). All rights reserved.
> This repository is licensed under the **[PolyForm Strict License 1.0.0](LICENSE)** as of **2026-11-24**.
>
> - **❌ No commercial use** — you may not use this plugin (source, prompt pools, scene / character / outfit libraries, presets, or any derivative) for any commercial purpose: paid distribution, bundling, paid generation services, commercial courses, or as the technical base of a commercial service. Written permission is required.
> - **❌ No repackaging or redistribution** — do not repackage, rename, wrap, re-upload, or include this plugin in third-party bundles, mirror archives, net-disk collections or resource sites, **modified or not**.
> - **❌ Do not remove or alter** this notice, the author attribution, or the source link.
> - **✅ Permitted** — personal non-commercial use (research, study, hobby, generating your own images) and modifying the source for your own use; quoting screenshots / recordings for non-commercial sharing with full attribution and this notice intact.
>
> **Commercial / derivative-work / repost licensing** → [Bilibili DM](https://space.bilibili.com/516891894) for written permission.
> **Legacy versions** published before **2026-11-24** remain under the license they were published with (MIT); that grant is not withdrawn.
> Full terms: **[NOTICE.md](NOTICE.md)**.

---

## What it is

A ComfyUI custom node that samples a complete ANIMA3 prompt body across five NSFW tiers and
feeds it into the "global positive prompt" node of an `Anima_工作流V3` workflow.

Fully local · zero dependencies · no network access at runtime ·
**generates prompts only, never images**.

## What's inside

| Item | Count |
| --- | --- |
| Lexicon entries | **987** |
| Characters | **2,383** |
| Scene presets | **122** |

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lingdongbudong/Comfyui-Anima-Wardrobe-V3.git
```

Restart ComfyUI. No dependencies to install. Sample workflows live in `workflows/`.

> ⚠️ **Adult content.** The lexicon includes explicit tiers (up to tier 4). The default tier
> is `sfw`; all characters are fictional adults. You are responsible for compliance with
> local law and platform policy.

## Language

Full documentation is in Chinese: **[README.md](README.md)**.

## License

**[PolyForm Strict License 1.0.0](LICENSE)** © 2026 灵冻不冻 (lingdongbudong).

- ❌ **No commercial use** without prior written permission.
- ❌ **No repackaging or redistribution**, modified or not.
- ✅ Personal non-commercial use, and modifying the source for your own use.
- Legacy versions published before 2026-11-24 remain under their original license (MIT);
  that grant is not withdrawn.

Commercial / derivative licensing: contact the author via [Bilibili DM](https://space.bilibili.com/516891894).
Full terms: [NOTICE.md](NOTICE.md) · Chinese documentation: [README.md](README.md).
