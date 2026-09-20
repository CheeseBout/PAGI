/**
 * Copyright(c) Live2D Inc. All rights reserved.
 *
 * Use of this source code is governed by the Live2D Open Software license
 * that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
 *
 * Trimmed for PAGI (2D_PLAN.md §3.2): dropped every constant that only the
 * Demo app's multi-model scene switcher / debug panel needs (ResourcesPath,
 * ModelDir[], BackImageName, GearImageName, view-drag constants...). PAGI has
 * no scene switcher — model path comes from Agent.avatar_config (SPEC §17.2).
 *
 * Mouse interaction (SPEC §20.14, Phase 21) restored the three constants
 * below — dropped in the original trim alongside the touch UI, but needed
 * again now that `AvatarCanvas.tsx` reacts to hover/tap.
 */

import { LogLevel } from '@framework/live2dcubismframework';

// Shader files are served as static assets (frontend/public/live2d/shaders/,
// copied verbatim from the SDK's Framework/Shaders/WebGL/) — same pattern as
// live2dcubismcore.min.js.
export const ShaderPath = '/live2d/shaders/';

// 外部定義ファイル（json）と合わせる
export const MotionGroupIdle = 'Idle';
// SPEC §20.14.4 — standard Cubism hit-area/motion-group names; models that
// don't declare them just don't react (§20.14.6), no fallback needed.
export const MotionGroupTapBody = 'TapBody';
export const HitAreaNameHead = 'Head';
export const HitAreaNameBody = 'Body';

// モーションの優先度定数
export const PriorityNone = 0;
export const PriorityIdle = 1;
export const PriorityNormal = 2;
export const PriorityForce = 3;

// MOC3の整合性検証オプション
export const MOCConsistencyValidationEnable = true;
// motion3.jsonの整合性検証オプション
export const MotionConsistencyValidationEnable = true;

// デバッグ用ログの表示オプション
export const DebugLogEnable = false;

// Frameworkから出力するログのレベル設定
export const CubismLoggingLevel: LogLevel = LogLevel.LogLevel_Warning;
