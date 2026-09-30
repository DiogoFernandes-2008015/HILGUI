"""app_pc.py – Planta discreta de 14 estados sincronizada para o PC."""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from cfg_raquel import CFG
from hil import set_realtime_priority

NAMES_U = [f"y_{i+1}" for i in range(12)]  # PC -> Pi (12 Medições)
NAMES_Y = ["u1", "u2", "u3", "u4"]         # Pi -> PC (4 Ações de Controlo)

# -----------------------------------------------------------------------------
# Carregamento de Matrizes
# -----------------------------------------------------------------------------
def _get_matrizes_dir():
    candidates = [
        Path(__file__).resolve().parent / "matrizes",
        Path(__file__).resolve().parents[1] / "matrizes",
        Path("matrizes"),
    ]
    for d in candidates:
        if d.exists():
            return d
    return Path(__file__).resolve().parent / "matrizes"

M_DIR = _get_matrizes_dir()

def load_matrix(name):
    p = M_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"Matriz não encontrada em: {p}")
    return np.loadtxt(p, delimiter=",", ndmin=2)

class Plant:
    def __init__(self):
        self.Ad = load_matrix("Ad.csv")
        self.Bd = load_matrix("Bd.csv")
        self.C = load_matrix("Cnav.csv")
        try:
            self.x0 = np.loadtxt(M_DIR / "x0.csv", delimiter=",").reshape(-1)
        except Exception:
            self.x0 = np.zeros(14)
        self.reset()

    def reset(self):
        self.x = self.x0.copy()
        self.k = 0
        print("[DEBUG] Planta resetada! k=0", flush=True)

    def get_y(self, t):
        return self.C @ self.x

    def step(self, t, u_recv):
        # Garante a adequação do vetor de controlo mesmo que venha com tamanho diferente
        if u_recv is not None and len(u_recv) > 0:
            u = np.array(u_recv, dtype=float)
            if len(u) >= 4:
                u = u[:4]
            else:
                u = np.pad(u, (0, 4 - len(u)))
        else:
            u = np.zeros(4)

        self.x = self.Ad @ self.x + self.Bd @ u

        # Imprime os primeiros passos para confirmar a execução
        if self.k < 3:
            print(f"[DEBUG] Step executado: k={self.k}, t={t:.2f}s, u_aplicado={u}", flush=True)

        if self.k == int(round(1.0 / CFG["dt"])):
            self.x[13] += 0.20
            print(f"*** PERTURBAÇÃO 1 APLICADA (k={self.k}, t={t:.2f}s) ***", flush=True)
        elif self.k == int(round(3.0 /CFG["dt"])):
            self.x[9] += 0.08
            print(f"*** PERTURBAÇÃO 2 APLICADA (k={self.k}, t={t:.2f}s) ***", flush=True)

        self.k += 1

_plant = Plant()

# -----------------------------------------------------------------------------
# Interface HIL
# -----------------------------------------------------------------------------
def reset():
    _plant.reset()

def source(t):
    """Envia as medições atuais para a Pi."""
    return _plant.get_y(t)

def sink(t, *args):
    """Aplica o controlo recebido da Pi (u_recv) e evolui a planta.
    Compatível com chamadas de 2 ou 3 argumentos do hil.py / hil_gui.py.
    """
    u_recv = args[-1] if args else None
    _plant.step(t, u_recv)

def post(log):
    if len(log["t"]) == 0:
        return

    t = log["t"]
    y_meas = log["u"]  # 12 Medições enviadas
    u_ctrl = log["y"]  # 4 Sinais de controlo recebidos
    rtt = log["rtt"]

    fig, axs = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    # Medições dos Sensores
    for i in range(y_meas.shape[1] if y_meas.ndim > 1 else 0):
        axs[0].plot(t, y_meas[:, i], label=f"y_{i+1}")
    axs[0].set_ylabel("Medições [y]")
    axs[0].set_title("Medições de Sensores da Planta (PC -> Pi)")
    axs[0].grid(True)

    # Ações de Controlo
    for i in range(u_ctrl.shape[1] if u_ctrl.ndim > 1 else 0):
        axs[1].plot(t, u_ctrl[:, i], label=f"u_{i+1}")
    axs[1].set_ylabel("Controlo [u]")
    axs[1].set_title("Ações de Controlo Recebidas (Pi -> PC)")
    axs[1].grid(True)
    axs[1].legend(loc="upper right", ncol=4)

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