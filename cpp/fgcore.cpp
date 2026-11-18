// =============================================================================
// cpp/fgcore.cpp · Núcleo numérico de FG VIEW en C++ (expuesto a Python con pybind11)
// -----------------------------------------------------------------------------
// Acelera los bucles más pesados del motor:
//   * TradeManager     -> gestión TP 50/30/20 + SL a break-even, simulada sobre
//                         cientos o miles de velas (estadística, validación, ROI).
//   * LadderSimulator  -> Monte Carlo del crecimiento exponencial (miles de caminos).
// La lógica es idéntica a engine/fgview/manager.py y engine/fgview/ladder.py; si este
// módulo no está compilado, Python usa automáticamente su propia versión.
//
// Compilar:  python setup_fgcore.py build_ext --inplace
// =============================================================================
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <vector>

namespace py = pybind11;
using Arr = py::array_t<double, py::array::c_style | py::array::forcecast>;

// Plan relativo a la entrada: mismas distancias % y mismo tamaño para cualquier precio
struct RelativePlan {
    int side;                       // +1 LONG, -1 SHORT
    double sl;                      // Distancia al SL (fracción)
    std::array<double, 3> tp;       // Distancias a TP1, TP2, TP3 (fracción)
    std::array<double, 3> tp_pnl;   // PnL neto de cada parte que cierra
    double sl_pnl;                  // PnL neto si salta el SL con toda la posición
    double fees;                    // Comisiones totales (USDT)
    double notional;                // Tamaño de la posición (USDT)
    double fee;                     // Comisión por lado (fracción) -> break-even
};

// Resultado: código de motivo 0=SL, 1=TP1+BE, 2=TP1+TP2+resto en TP1, 3=TP1+TP2+TP3, 4=tiempo
struct Outcome {
    double pnl;
    int exit_index;
    int tps_hit;
    int reason;
};

class TradeManager {
public:
    explicit TradeManager(int horizon) : horizon_(horizon) {
        if (horizon <= 0) throw std::invalid_argument("horizon debe ser > 0");
    }
    int horizon() const { return horizon_; }

    // Primitivo común: +1 objetivo primero, -1 stop primero, 0 ninguno (stop gana en empate)
    static std::pair<int, int> first_hit(int side, const double* h, const double* l, int start, int end,
                                         double target, double stop) {
        for (int j = start + 1; j <= end; ++j) {
            if (side == 1 ? l[j] <= stop : h[j] >= stop) return {-1, j};
            if (side == 1 ? h[j] >= target : l[j] <= target) return {1, j};
        }
        return {0, end};
    }

    Outcome simulate(const RelativePlan& p, const double* h, const double* l, const double* c, int n, int start,
                     double entry) const {
        const int end = std::min(start + horizon_, n - 1);
        const double s = p.side;
        const std::array<double, 3> tp_px = {entry * (1 + s * p.tp[0]), entry * (1 + s * p.tp[1]),
                                             entry * (1 + s * p.tp[2])};
        const std::array<double, 3> stops = {entry * (1 - s * p.sl), entry * (1 + s * 2 * p.fee), tp_px[0]};
        const std::array<double, 3> remaining = {1.0, 0.5, 0.2};
        double pnl = 0.0;
        int i = start;
        for (int k = 0; k < 3; ++k) {
            auto [res, j] = first_hit(p.side, h, l, i, end, tp_px[k], stops[k]);
            i = j;
            if (res == 1) {
                pnl += p.tp_pnl[k];
                if (k == 2) return {pnl, i, 3, 3};
                continue;
            }
            if (res == -1) {
                if (k == 0) return {pnl + p.sl_pnl, i, 0, 0};
                if (k == 1) return {pnl, i, 1, 1};
                return {pnl + p.notional * 0.2 * p.tp[0] - p.fees * 0.2, i, 2, 2};
            }
            const double rem = remaining[k];
            const double move = s * (c[end] - entry) / entry;
            return {pnl + p.notional * rem * move - p.fees * rem, end, k, 4};
        }
        return {pnl, i, 3, 3};
    }

    // Simula el plan desde muchas velas a la vez. Devuelve (pnl, tps_hit, reason, exit_index)
    py::tuple evaluate(int side, double sl, std::vector<double> tp, std::vector<double> tp_pnl, double sl_pnl,
                       double fees, double notional, double fee, Arr high, Arr low, Arr close,
                       std::vector<int> starts, std::vector<double> entries) const {
        if (tp.size() != 3 || tp_pnl.size() != 3) throw std::invalid_argument("se esperan 3 TPs");
        if (entries.size() != starts.size()) throw std::invalid_argument("starts y entries deben medir lo mismo");
        const int n = static_cast<int>(close.size());
        if (high.size() != close.size() || low.size() != close.size())
            throw std::invalid_argument("high, low y close deben medir lo mismo");
        const RelativePlan p{side, sl, {tp[0], tp[1], tp[2]}, {tp_pnl[0], tp_pnl[1], tp_pnl[2]}, sl_pnl, fees, notional, fee};
        const double *h = high.data(), *l = low.data(), *c = close.data();
        const size_t m = starts.size();
        py::array_t<double> pnl(m);
        py::array_t<int> tps(m), reason(m), exit_idx(m);
        auto P = pnl.mutable_unchecked<1>();
        auto T = tps.mutable_unchecked<1>();
        auto R = reason.mutable_unchecked<1>();
        auto E = exit_idx.mutable_unchecked<1>();
        {
            py::gil_scoped_release release;  // Permite paralelismo real desde hilos de Python
            for (size_t k = 0; k < m; ++k) {
                const int st = starts[k];
                if (st < 0 || st >= n) {
                    P(k) = 0.0; T(k) = 0; R(k) = 4; E(k) = st;
                    continue;
                }
                const Outcome o = simulate(p, h, l, c, n, st, entries[k]);
                P(k) = o.pnl; T(k) = o.tps_hit; R(k) = o.reason; E(k) = o.exit_index;
            }
        }
        return py::make_tuple(pnl, tps, reason, exit_idx);
    }

    // % de veces que +tp se alcanzó antes que el SL en `horizon` velas -> (tp %, sl %, casos)
    py::tuple reach(int side, Arr high, Arr low, Arr close, double tp, double sl,
                    py::array_t<bool, py::array::c_style | py::array::forcecast> mask, bool use_mask) const {
        const int n = static_cast<int>(close.size());
        const double *h = high.data(), *l = low.data(), *c = close.data();
        const bool* mk = use_mask ? mask.data() : nullptr;
        long hits = 0, stops = 0, total = 0;
        {
            py::gil_scoped_release release;
            for (int i = 0; i < n - horizon_; ++i) {
                if (mk && !mk[i]) continue;
                auto [res, j] = first_hit(side, h, l, i, i + horizon_, c[i] * (1 + side * tp), c[i] * (1 - side * sl));
                (void)j;
                ++total;
                hits += res == 1;
                stops += res == -1;
            }
        }
        const double t = total ? static_cast<double>(total) : 1.0;
        return py::make_tuple(total ? hits / t * 100.0 : 0.0, total ? stops / t * 100.0 : 0.0, total);
    }

private:
    int horizon_;
};

// -----------------------------------------------------------------------------
// Monte Carlo del crecimiento exponencial (escalera)
// -----------------------------------------------------------------------------
class LadderSimulator {
public:
    explicit LadderSimulator(double min_stake) : min_stake_(min_stake) {}
    double min_stake() const { return min_stake_; }

    // `wins` es la matriz (caminos × operaciones) de aciertos generada en Python con la
    // misma semilla: así C++ y Python dan EXACTAMENTE el mismo resultado.
    py::dict simulate(py::array_t<bool, py::array::c_style | py::array::forcecast> wins, double capital,
                      double target, double roi_win, double roi_loss, double vault_pct, bool protected_mode) const {
        if (wins.ndim() != 2) throw std::invalid_argument("wins debe ser una matriz 2D");
        const auto paths = static_cast<long>(wins.shape(0));
        const auto max_trades = static_cast<long>(wins.shape(1));
        const bool* w = wins.data();
        const double vault_share = protected_mode ? vault_pct / 100.0 : 0.0;
        long reached = 0, ruined = 0;
        std::vector<double> trades_needed, finals_fail;
        trades_needed.reserve(paths);
        {
            py::gil_scoped_release release;
            for (long k = 0; k < paths; ++k) {
                double stake = capital, vault = 0.0;
                int streak = 0;
                bool done = false;
                for (long t = 0; t < max_trades; ++t) {
                    const double at_risk = stake * ((protected_mode && streak >= 2) ? 0.5 : 1.0);
                    if (w[k * max_trades + t]) {
                        const double gain = at_risk * roi_win;
                        stake += gain * (1.0 - vault_share);
                        vault += gain * vault_share;
                        streak = 0;
                    } else {
                        stake += at_risk * roi_loss;
                        ++streak;
                    }
                    if (stake + vault >= target) {
                        ++reached;
                        trades_needed.push_back(static_cast<double>(t + 1));
                        done = true;
                        break;
                    }
                    if (stake < min_stake_) {
                        if (protected_mode && vault >= min_stake_ * 2) {  // Recarga con media caja fuerte
                            stake += vault / 2;
                            vault /= 2;
                        } else {
                            ++ruined;
                            break;
                        }
                    }
                }
                if (!done) finals_fail.push_back(stake + vault);
            }
        }
        auto median = [](std::vector<double> v) {
            if (v.empty()) return std::nan("");
            std::sort(v.begin(), v.end());
            const size_t m = v.size() / 2;
            return v.size() % 2 ? v[m] : (v[m - 1] + v[m]) / 2.0;
        };
        const double n = paths ? static_cast<double>(paths) : 1.0;
        py::dict out;
        out["success_pct"] = reached * 100.0 / n;
        out["ruin_pct"] = ruined * 100.0 / n;
        out["median_trades"] = median(trades_needed);
        out["kept_if_fail"] = median(finals_fail);
        return out;
    }

private:
    double min_stake_;
};

PYBIND11_MODULE(fgcore, m) {
    m.doc() = "Núcleo C++ de FG VIEW: gestión de operaciones y Monte Carlo (POO, pybind11)";
    m.attr("__version__") = "1.0.0";
    py::class_<TradeManager>(m, "TradeManager")
        .def(py::init<int>(), py::arg("horizon"))
        .def_property_readonly("horizon", &TradeManager::horizon)
        .def("evaluate", &TradeManager::evaluate, py::arg("side"), py::arg("sl"), py::arg("tp"), py::arg("tp_pnl"),
             py::arg("sl_pnl"), py::arg("fees"), py::arg("notional"), py::arg("fee"), py::arg("high"), py::arg("low"),
             py::arg("close"), py::arg("starts"), py::arg("entries"))
        .def("reach", &TradeManager::reach, py::arg("side"), py::arg("high"), py::arg("low"), py::arg("close"),
             py::arg("tp"), py::arg("sl"), py::arg("mask"), py::arg("use_mask"));
    py::class_<LadderSimulator>(m, "LadderSimulator")
        .def(py::init<double>(), py::arg("min_stake"))
        .def_property_readonly("min_stake", &LadderSimulator::min_stake)
        .def("simulate", &LadderSimulator::simulate, py::arg("wins"), py::arg("capital"), py::arg("target"),
             py::arg("roi_win"), py::arg("roi_loss"), py::arg("vault_pct"), py::arg("protected_mode"));
}
