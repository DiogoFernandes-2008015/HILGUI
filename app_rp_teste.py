"""app_pi.py – Controlador LQR + Observador de Luenberger (Raspberry Pi)."""

from pathlib import Path
import numpy as np
from cfg_raquel import CFG
from hil import Slave, set_realtime_priority

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


class Ctrl:
    def __init__(self):
        self.K = load_matrix("K.csv")          # (4, 14)
        self.Aobs = load_matrix("Aobs_d.csv")   # (14, 14)
        self.Bobs_u = load_matrix("Bobs_u.csv") # (14, 4)
        self.Bobs_y = load_matrix("Bobs_y.csv") # (14, 12)
        self.C = load_matrix("Cnav.csv")        # (12, 14)
        self.reset(CFG["dt"])

    def reset(self, dt):
        """Reinicia o estado do observador de estados."""
        self.dt = dt
        self.xhat = np.zeros(14)

    def step(self, u):
        """
        u: 12 medições recebidas do PC (y)
        retorna: 4 ações de controle LQR enviadas de volta ao PC
        """
        y = np.array(u, dtype=float)

        # Lei de controle LQR
        u_control = -self.K @ self.xhat

        # Atualização do Observador de Luenberger
        self.xhat = self.Aobs @ self.xhat + self.Bobs_u @ u_control + self.Bobs_y @ y

        return u_control


if __name__ == "__main__":
    set_realtime_priority()
    c = Ctrl()
    Slave(CFG).run(c.step, reset=c.reset)