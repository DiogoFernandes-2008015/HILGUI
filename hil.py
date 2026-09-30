"""hil.py – infraestrutura HIL via UDP. Arquivo idêntico no PC e na Raspberry Pi.

Protocolo:
  - Pacotes de controle (CONNECT/START/STOP): tipo, seq, valor (no START, o valor é o dt).
  - Pacotes de dados: tipo, seq, n doubles. A Pi devolve o MESMO seq recebido e acrescenta,
    como último valor, o tempo que gastou calculando (permite separar rede x processamento).
"""
import socket
import struct
import time
import os
import sys
import numpy as np

def set_realtime_priority(priority=80):
    """Tenta definir a prioridade de tempo real (SCHED_FIFO) no Linux / Raspberry Pi."""
    if sys.platform.startswith("linux"):
        try:
            param = os.sched_param(priority)
            os.sched_setscheduler(0, os.SCHED_FIFO, param)
            print(f"[RT] Prioridade de tempo real SCHED_FIFO ({priority}) ativada.")
        except PermissionError:
            print("[RT] Aviso: Execute como root (sudo) para ativar prioridade tempo real.")
        except Exception as e:
            print(f"[RT] Aviso: Não foi possível definir prioridade RT: {e}")

CONNECT, START, DATA, STOP = 1, 2, 3, 4
_CTRL = struct.Struct("<BId")

class _Link:
    def __init__(self, local_port, peer, n_send, n_recv):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", local_port))
        
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            
        self.peer = peer
        self.n_send = n_send
        self.tx = struct.Struct(f"<BI{n_send}d")
        self.rx = struct.Struct(f"<BI{n_recv}d")
        self.seq = 0

    def ctrl(self, kind, value=0.0):
        try:
            self.sock.sendto(_CTRL.pack(kind, 0, value), self.peer)
        except OSError:
            pass

    def send(self, values, seq=None):
        if len(values) != self.n_send:
            raise ValueError(f"Esperava {self.n_send} valores, recebi {len(values)}.")
        if seq is None:
            self.seq += 1
            seq = self.seq
        self.sock.sendto(self.tx.pack(DATA, seq, *values), self.peer)
        return seq

    def recv(self):
        raw, _ = self.sock.recvfrom(4096)
        kind = raw[0]
        if kind == DATA:
            if len(raw) != self.rx.size:
                raise ValueError("Pacote com tamanho inesperado.")
            _, seq, *vals = self.rx.unpack(raw)
            return kind, seq, np.array(vals)
        _, seq, val = _CTRL.unpack(raw)
        return kind, seq, val

    def flush(self):
        self.sock.setblocking(False)
        while True:
            try:
                self.sock.recvfrom(4096)
            except (BlockingIOError, ConnectionResetError, OSError):
                break

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

def _wait_until(t_target):
    while True:
        rest = t_target - time.perf_counter()
        if rest <= 0:
            return
        if rest > 0.002:
            time.sleep(rest - 0.002)
class Master:
    """Lado PC."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.link = _Link(cfg["pc"][1], cfg["pi"], cfg["n_pc2pi"], cfg["n_pi2pc"] + 1)
        self.rows = []          # (t, u, y, rtt, t_pi) por amostra – lido pela interface
        self.lost = self.stale = self.overruns = 0

    def close(self):
        self.link.close()

    def wait_pi(self, stop=None):
        """Bloqueia até chegar um CONNECT novo da Pi. Retorna False se `stop` for acionado."""
        L = self.link
        L.flush()
        L.sock.settimeout(0.3)
        while not (stop is not None and stop.is_set()):
            try:
                kind, _, _ = L.recv()
            except (socket.timeout, ConnectionResetError):
                continue
            if kind == CONNECT:
                return True
        return False

    def _wait_reply(self, seq, deadline):
        L = self.link
        while True:
            rest = deadline - time.perf_counter()
            if rest <= 0:
                self.lost += 1
                return None
            L.sock.settimeout(rest)
            try:
                kind, s, v = L.recv()
            except socket.timeout:
                self.lost += 1
                return None
            except ConnectionResetError:
                continue
            if kind == DATA:
                if s == seq:
                    return v
                self.stale += 1          # resposta de um passo anterior que chegou atrasada

    def run(self, source, sink=None, stop=None, save_as=None):
        """source(t) -> n_pc2pi valores.  sink(t, u, y) opcional.  Retorna o log (dict)."""
        c, L = self.cfg, self.link
        dt, T, tmo = c["dt"], c["t_sim"], c.get("timeout", 0.05)
        self.rows = []
        self.lost = self.stale = self.overruns = 0
        L.flush()
        L.ctrl(START, dt)
        t0 = time.perf_counter()
        t_next = t0
        try:
            while not (stop is not None and stop.is_set()):
                t_send = time.perf_counter()
                t = t_send - t0
                if t >= T:
                    break
                u = np.asarray(source(t), dtype=float).ravel()
                seq = L.send(u)
                y = self._wait_reply(seq, t_send + tmo)
                if y is not None:
                    rtt = time.perf_counter() - t_send
                    self.rows.append((t, u, y[:-1], rtt, y[-1]))
                    if sink:
                        sink(t, u, y[:-1])
                t_next += dt
                if time.perf_counter() > t_next:      # passo estourou o dt
                    self.overruns += 1
                    t_next = time.perf_counter()
                else:
                    _wait_until(t_next)
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
        finally:
            L.ctrl(STOP)
            log = self.build_log()
            if save_as:
                np.savez(save_as, **log)
            if len(log["t"]):
                r = log["rtt"] * 1e3
                print(f"\nAmostras: {len(r)} | perdidas: {self.lost} | atrasadas: {self.stale} "
                      f"| overruns: {self.overruns}\nRTT médio {r.mean():.3f} ms | "
                      f"p99 {np.percentile(r, 99):.3f} ms | máx {r.max():.3f} ms")
        return log

    def build_log(self):
        rows = list(self.rows)
        col = lambda i: np.array([r[i] for r in rows])
        return dict(t=col(0), u=col(1), y=col(2), rtt=col(3), t_pi=col(4),
                    dt=self.cfg["dt"], lost=self.lost, stale=self.stale,
                    overruns=self.overruns)


class Slave:
    """Lado Raspberry Pi."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.link = _Link(cfg["pi"][1], cfg["pc"], cfg["n_pi2pc"] + 1, cfg["n_pc2pi"])

    def _handshake(self):
        L = self.link
        L.flush()
        L.sock.settimeout(1.0)
        print("Procurando o PC...")
        while True:
            L.ctrl(CONNECT)
            try:
                kind, _, val = L.recv()
            except socket.timeout:
                print("Aguardando liberação do usuário no PC...")
                continue
            except ConnectionResetError:
                continue
            if kind == START:
                print(f"Conexão autorizada! dt = {val * 1e3:.3f} ms")
                return val

    def run(self, step, reset=None, forever=True):
        """step(u) -> n_pi2pc valores.  reset(dt) é chamado a cada START."""
        L = self.link
        try:
            while True:
                dt = self._handshake()
                if reset:
                    reset(dt)
                L.sock.settimeout(self.cfg.get("slave_timeout", 5.0))
                while True:
                    try:
                        kind, seq, u = L.recv()
                    except socket.timeout:
                        print("PC sem resposta; voltando ao handshake.")
                        break
                    except ConnectionResetError:
                        print("Ligar/Desligar detetado no PC; voltando ao handshake.")
                        break
                    if kind == STOP:
                        print("STOP recebido.")
                        break
                    if kind == DATA:
                        t0 = time.perf_counter()
                        y = np.asarray(step(u), dtype=float).ravel()
                        tp = time.perf_counter() - t0
                        L.send(np.append(y, tp), seq)
                if not forever:
                    break
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
        finally:
            L.close()