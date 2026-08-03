#!/usr/bin/env node
// WhatsApp 收发桥(独立子进程,由 services.runtime.whatsapp 拉起管理)。
//
// 用 Baileys(WhatsApp Web 多设备协议)以「已连接的设备」身份接入用户自己的
// WhatsApp 账号——与 OpenClaw 的 WhatsApp 通道同一接法:扫码配对,只把
// 「给自己发消息」(Message Yourself 自聊)当作指令通道,他人来信一概不转发。
//
// 会话凭证多文件持久化在 WA_AUTH_DIR(环境变量传入);事件按 NDJSON 写 stdout:
//   {type:"qr", qr}            待扫码(配对期间会周期性刷新)
//   {type:"open", jid, name}   已连接(jid 为本机号,自聊收发都走它)
//   {type:"close", code}       连接断开,进程内自动重连
//   {type:"msg", text, name}   自聊里用户发的文本
//   {type:"sent"} / {type:"send_err", error}
//   {type:"logged_out"}        手机端解除了设备绑定(rc=4,父进程清理会话)
//   {type:"fatal", error:"baileys_missing"}  未安装依赖(rc=3)
// stdin 命令:{type:"send", text}(发到自聊)、{type:"logout"}。
// 未注册状态下配对二维码整轮超时则以 rc=5 退出,由父进程决定是否再开一轮。

import { createInterface } from 'node:readline';

const emit = (o) => process.stdout.write(JSON.stringify(o) + '\n');

let baileys;
try {
  baileys = await import('baileys');
} catch {
  try {
    baileys = await import('@whiskeysockets/baileys');
  } catch {
    emit({ type: 'fatal', error: 'baileys_missing' });
    process.exit(3);
  }
}
const {
  default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion,
  DisconnectReason, jidNormalizedUser,
} = baileys;

const AUTH_DIR = process.env.WA_AUTH_DIR;
if (!AUTH_DIR) { emit({ type: 'fatal', error: 'missing_auth_dir' }); process.exit(2); }

// Baileys 需要 pino 形状的 logger;全静默,诊断信息统一走 NDJSON 事件
const silent = {
  level: 'silent', child() { return this; },
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
};

let sock = null;
let selfJid = '';
let selfJids = new Set();                // 本人身份的全部 jid:手机号形式 + LID 匿名形式
const sentIds = new Set();               // 本桥发出的消息 id,防自聊回声

const norm = (j) => { try { return j ? jidNormalizedUser(j) : ''; } catch { return ''; } };
const dbg = (s) => process.stderr.write(`[${new Date().toISOString()}] ${s}\n`);

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));
  sock = makeWASocket({
    auth: state, version, logger: silent, printQRInTerminal: false,
    syncFullHistory: false, markOnlineOnConnect: false,
  });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (u) => {
    if (u.qr) emit({ type: 'qr', qr: u.qr });
    if (u.connection === 'open') {
      selfJid = norm(sock.user?.id);
      // 自聊消息的 remoteJid 可能是手机号形式,也可能是 LID 匿名形式(@lid),都算本人
      selfJids = new Set([selfJid, norm(sock.user?.lid)].filter(Boolean));
      dbg(`open self=${[...selfJids].join(',')}`);
      emit({ type: 'open', jid: selfJid, name: sock.user?.name || '' });
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode || 0;
      if (code === DisconnectReason.loggedOut) { emit({ type: 'logged_out' }); process.exit(4); }
      // 配对成功瞬间会保存凭证并断开一次(restartRequired),此时已 registered,走重连;
      // 仍未 registered 说明这轮二维码超时/中断,rc=5 交父进程决定是否再开一轮
      if (!state.creds?.registered) process.exit(5);
      emit({ type: 'close', code });
      setTimeout(() => start().catch((e) => {
        emit({ type: 'err', error: String(e?.message || e) }); process.exit(1);
      }), 2000);
    }
  });
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    for (const m of messages) {
      const jid = norm(m.key?.remoteJid || '');
      const self = selfJids.has(jid);
      dbg(`upsert type=${type} jid=${jid} self=${self} fromMe=${!!m.key?.fromMe} id=${m.key?.id || ''}`);
      if (type !== 'notify') continue;                   // 历史/补录不当作新指令
      if (!self) continue;                               // 仅自聊,他人来信不转发
      if (m.key?.id && sentIds.has(m.key.id)) continue;  // 本桥自己发的
      let msg = m.message || {};
      if (msg.ephemeralMessage) msg = msg.ephemeralMessage.message || {};
      const text = msg.conversation || msg.extendedTextMessage?.text || '';
      if (text) emit({ type: 'msg', text, name: m.pushName || '' });
    }
  });
}

createInterface({ input: process.stdin }).on('line', async (l) => {
  let c;
  try { c = JSON.parse(l); } catch { return; }
  if (c.type === 'send') {
    try {
      if (!sock || !selfJid) throw new Error('not connected');
      const r = await sock.sendMessage(selfJid, { text: String(c.text ?? '') });
      if (r?.key?.id) {
        sentIds.add(r.key.id);
        if (sentIds.size > 500) for (const id of sentIds) { sentIds.delete(id); if (sentIds.size <= 250) break; }
      }
      emit({ type: 'sent' });
    } catch (e) {
      emit({ type: 'send_err', error: String(e?.message || e) });
    }
  } else if (c.type === 'logout') {
    try { await sock?.logout(); } catch { /* 已断开也视为成功 */ }
    emit({ type: 'logged_out' });
    process.exit(4);
  }
});

start().catch((e) => { emit({ type: 'err', error: String(e?.message || e) }); process.exit(1); });
