/**
 * Copyright(c) Live2D Inc. All rights reserved.
 *
 * Use of this source code is governed by the Live2D Open Software license
 * that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
 *
 * Trimmed for PAGI (2D_PLAN.md §3.2): dropped every constant that only the
 * Demo app's multi-model scene switcher / touch UI / debug panel needs
 * (ResourcesPath, ModelDir[], BackImageName, GearImageName, view-drag
 * constants...). PAGI has no scene switcher — model path comes from
 * Agent.avatar_config (SPEC §17.2) — and no touch interaction.
 */

import { LogLevel } from '@framework/live2dcubismframework';

// Shader files are served as static assets (frontend/public/live2d/shaders/,
// copied verbatim from the SDK's Framework/Shaders/WebGL/) — same pattern as
// live2dcubismcore.min.js.
export const ShaderPath = '/live2d/shaders/';

// 外部定義ファイル（json）と合わせる
export const MotionGroupIdle = 'Idle';

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
