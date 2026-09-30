"""app_pc.py – Planta discreta de pêndulo invertido sincronizada para o PC."""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from cfg import CFG
from hil import set_realtime_priority

NAMES_U = ["x", "xp", "th", "thp", "thref"]  # PC -> Pi (5 sinais)
NAMES_Y = ["u"]                             # Pi -> PC (1 ação de controlo)

class Plant:
    def __init__(self):
        self.A = np.array([[0, 1, 0, 0],
                           [0, -0.1818, 2.6754, 0],
                           [0, 0, 0, 1],
                           [0, -0.4545, 31.2136, 0]])
        self.B = np.array([[0], [1.8182], [0], [4.5454]])  # Dimensão (4, 1)
        self.C = np.eye(4)
        self.x0 = np.array([[0], [0], [-np.pi/2], [0]])   # Dimensão (4, 1)
        self.reset()

    def reset(self):
        self.x = self.x0.copy()
        self.k = 0

    def get_y(self, t):
        return (self.C @ self.x).flatten()  # Retorna vetor 1D de 4 elementos

    def step(self, t, u_recv):
        u_val = 0.0
        if u_recv is not None and len(u_recv) > 0:
            u_val = float(u_recv[0])
            
        # Garante a forma (1, 1) para a multiplicação matricial B @ u
        u = np.array([[u_val]])
        xp = self.A @ self.x + self.B @ u

        self.x = self.x + xp * CFG["dt"]
        self.k += 1

_plant = Plant()

# -----------------------------------------------------------------------------
# Interface HIL
# -----------------------------------------------------------------------------
def reset():
    _plant.reset()

def get_theta_ref(t=0):
    return 0.0

def source(t):
    """Envia as medições atuais para a Pi (4 estados + 1 ref = 5 valores)."""
    states = _plant.get_y(t)
    ref = get_theta_ref(t)
    return np.append(states, ref)

def sink(t, *args):
    """Aplica o controlo recebido da Pi (u_recv) e evolui a planta."""
    u_recv = args[-1] if args else None
    _plant.step(t, u_recv)

def post(log):
    if len(log["t"]) == 0:
        return

    t = log["t"]
    y_meas = log["u"]  # Medições enviadas (PC -> Pi)
    u_ctrl = log["y"]  # Ação de controlo (Pi -> PC)
    rtt = log["rtt"]

    fig, axs = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    # Medições dos Sensores
    for i in range(y_meas.shape[1] if y_meas.ndim > 1 else 0):
        label_name = NAMES_U[i] if i < len(NAMES_U) else f"y_{i+1}"
        axs[0].plot(t, y_meas[:, i], label=label_name)
    axs[0].set_ylabel("Medições [y]")
    axs[0].set_title("Medições de Sensores da Planta (PC -> Pi)")
    axs[0].grid(True)
    axs[0].legend(loc="upper right")

    # Ações de Controlo
    for i in range(u_ctrl.shape[1] if u_ctrl.ndim > 1 else 0):
        axs[1].plot(t, u_ctrl[:, i], label=f"u_{i+1}")
    axs[1].set_ylabel("Controlo [u]")
    axs[1].set_title("Ações de Controlo Recebidas (Pi -> PC)")
    axs[1].grid(True)
    axs[1].legend(loc="upper right")

    # RTT UDP
    axs[2].plot(t, rtt * 1000.0, "r-", label="RTT UDP")
    axs[2].axhline(CFG["dt"] * 1000.0, color="k", linestyle="--", label=f"dt = {CFG['dt']*1000:.0f} ms")
    axs[2].set_ylabel("Latência [ms]")
    axs[2].set_xlabel("Tempo [s]")
    axs[2].set_title("Latência de Comunicação (RTT)")
    axs[2].grid(True)
    axs[2].legend()

    fig.tight_layout()

if __name__ == "__main__":
    set_realtime_priority()
    from hil import Master
    m = Master(CFG)
    print("Aguardando a Raspberry Pi...")
    m.wait_pi()
    input("Pi conectada. ENTER para iniciar...")
    reset()
    post(m.run(source, sink=sink, save_as="log_hil.npz"))
    plt.show()
    m.close()