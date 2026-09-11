"""equirect(全景)<-> 透视 的纯 numpy 重采样与坐标约定。

坐标系与白模一致(docs/whitebox.md):1 单位 = 1 m,Y 上,X = 图右,Z = 图下;yaw = atan2(dx, dz),yaw=0 朝 +Z,yaw=π/2 朝 +X。
物理相机:面朝 f、上 +Y 时右向量 = f × up;面朝 +Z 时右 = −X(与 three.js/白模一致)。
全景像素约定:u = ((yaw0 − az)/2π + 0.5) mod 1(向右转 = az 减小 = u 增大),v = 0.5 − el/π;
az = atan2(dx, dz),el = asin(dy);yaw0 = 全景中央列的世界朝向。
"""
from __future__ import annotations

import numpy as np
import cv2


def dirs_for_perspective(w: int, h: int, yaw: float, pitch: float, vfov_deg: float) -> np.ndarray:
    """返回 (h, w, 3) 单位方向向量(世界系),相机无 roll;vfov 为垂直视场角(度)。"""
    f = (h / 2) / np.tan(np.radians(vfov_deg) / 2)
    xs = np.arange(w) - (w - 1) / 2
    ys = np.arange(h) - (h - 1) / 2
    X, Y = np.meshgrid(xs, ys)
    # 相机局部:前 +z, 上 +y, 右 = 前×上 = −x(右手系物理相机)
    d = np.stack([-X, -Y, np.full_like(X, f, dtype=np.float64)], -1)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    cp, sp = np.cos(pitch), np.sin(pitch)      # 先 pitch(绕相机 x 轴)
    y2 = d[..., 1] * cp - d[..., 2] * sp
    z2 = d[..., 1] * sp + d[..., 2] * cp
    d = np.stack([d[..., 0], y2, z2], -1)
    cy, sy = np.cos(yaw), np.sin(yaw)          # 再 yaw(绕世界 Y 轴)
    x3 = d[..., 0] * cy + d[..., 2] * sy
    z3 = -d[..., 0] * sy + d[..., 2] * cy
    return np.stack([x3, d[..., 1], z3], -1)


def dir_to_uv(d: np.ndarray, yaw0: float = 0.0):
    az = np.arctan2(d[..., 0], d[..., 2])
    el = np.arcsin(np.clip(d[..., 1], -1, 1))
    u = ((yaw0 - az) / (2 * np.pi) + 0.5) % 1.0
    v = 0.5 - el / np.pi
    return u, v


def uv_to_dir(u, v, yaw0: float = 0.0) -> np.ndarray:
    az = yaw0 - (np.asarray(u) - 0.5) * 2 * np.pi
    el = (0.5 - np.asarray(v)) * np.pi
    return np.stack([np.cos(el) * np.sin(az), np.sin(el), np.cos(el) * np.cos(az)], -1)


def sample_equirect(pano: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    H, W = pano.shape[:2]
    mx = (u * W - 0.5).astype(np.float32)
    my = (v * H - 0.5).astype(np.float32)
    return cv2.remap(pano, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def perspective_from_equirect(pano, w, h, yaw, pitch, vfov_deg, yaw0=0.0):
    """crop 模式:从全景按 (yaw, pitch, vfov) 纯旋转重采样一张透视图。"""
    d = dirs_for_perspective(w, h, yaw, pitch, vfov_deg)
    u, v = dir_to_uv(d, yaw0)
    return sample_equirect(pano, u, v)


def camera_yaw_pitch(position, target):
    """白模 camera 关键帧(position, target)→ (yaw, pitch) 弧度。"""
    p = np.asarray(position, float)
    t = np.asarray(target, float)
    d = t - p
    return float(np.arctan2(d[0], d[2])), float(np.arcsin(d[1] / np.linalg.norm(d)))


def window_pixel_to_dir(px, py, w, h, yaw, pitch, vfov_deg) -> np.ndarray:
    """透视窗内像素 → 世界方向(用于把视觉模型给的框回投到全景)。"""
    d = dirs_for_perspective(w, h, yaw, pitch, vfov_deg)
    xi = int(np.clip(round(px), 0, w - 1))
    yi = int(np.clip(round(py), 0, h - 1))
    return d[yi, xi]


def focal_to_vfov(focal_mm: float, gate_mm: float = 24.0) -> float:
    """焦段 → 垂直 FOV(度),沿用白模 24 mm 竖向片门口径。"""
    return float(np.degrees(2 * np.arctan(gate_mm / (2 * focal_mm))))
