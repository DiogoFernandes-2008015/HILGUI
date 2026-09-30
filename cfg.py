import os

"""cfg.py – configuração única, copie o MESMO arquivo para o PC e para a Raspberry Pi."""

CFG = dict(
    pc=("192.168.1.50", 5005),    # IP real do PC e porta em que o PC escuta
    pi=("192.168.1.100", 5006),   # IP estático da Pi e porta em que a Pi escuta
    dt=0.001,                     # passo da simulação [s] (1 ms)
    t_sim=12.0,                    # duração [s] (4 segundos)
    timeout=0.05,                 # tempo máx. esperando a resposta de cada passo [s]
    slave_timeout=5.0,            # tempo máx. que a Pi espera por dados do PC
    n_pc2pi= 5,                    # nº de variáveis PC -> Pi
    n_pi2pc= 1,                    # nº de variáveis Pi -> PC
)

if os.environ.get("HIL_LOCAL"):   # modo de teste local sem Raspberry
    CFG["pc"] = ("127.0.0.1", 5005)
    CFG["pi"] = ("127.0.0.1", 5006)