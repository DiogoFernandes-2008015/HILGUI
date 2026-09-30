"""app_pi.py – aplicação corrigida na Raspberry Pi."""

import numpy as np
from hil import Slave, set_realtime_priority
from cfg import CFG


class Ctrl:
    def __init__(self):
        self.K = np.array([-1.0000, -2.0152, 28.5321, 5.1204])

    def step(self, u):
        # Desembalar o vetor recebido do PC (5 elementos)
        x, dx, theta, dtheta, theta_ref = u

        # Erro do ângulo do pêndulo
        e_theta = theta - theta_ref

        # Lei de controlo: u = -K * e
        u_ctrl = -self.K[0] * x - self.K[1] * dx - self.K[2] * e_theta - self.K[3] * dtheta

        # Saturação do esforço de controlo (força em N)
        u_ctrl = np.clip(u_ctrl, -20.0, 20.0)

        return [float(u_ctrl)]


if __name__ == "__main__":
    set_realtime_priority()
    c = Ctrl()
    Slave(CFG).run(c.step)