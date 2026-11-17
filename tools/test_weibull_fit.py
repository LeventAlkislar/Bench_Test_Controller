"""Step timing, robust residuals, source transforms, and block cache behavior."""
from datetime import datetime
import copy
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.measurement.log_parser import LogParser, ValveEvent
from bench_test.measurement.weibull_fit import FitOptions, _evaluate_model, a_port_states, evaluate, fit_steps, prepare_series, valve_states


BASE = 1700000000.


def event(minutes, port=None, position=None, source="session.log"):
    return ValveEvent(datetime.fromtimestamp(BASE+minutes*60), port, position, source)


def signal():
    options = FitOptions()
    minutes = np.arange(81, dtype=float)
    y = evaluate(minutes, np.array([20.]), np.array([150., 15.]), np.array([6.]), options)
    y += np.random.default_rng(12).normal(0, .3, len(y))
    y[10], y[12], y[14], y[50] = 15., 17., 16., 150.
    events = [event(-10.5, 1), event(-10.5, position="Load"), event(9.5, 2)]
    return BASE+minutes*60, y, events


def curve_at(result, minute):
    valid = np.isfinite(result.x) & np.isfinite(result.y)
    return np.interp(BASE+minute*60, result.x[valid], result.y[valid])


class WeibullFitTests(unittest.TestCase):
    def test_flat_and_negative_drift_are_fitted(self):
        minutes=np.arange(121,dtype=float)
        events=[event(-10.5,1),event(-10.5,position='Load')]
        for slope in (0.,-8.):
            with self.subTest(slope=slope):
                result=fit_steps(BASE+minutes*60,85.+slope*minutes/60,events)
                fitted=next(iter(result.cache.values()))['fit']
                self.assertAlmostEqual(fitted['slopes'][0],slope,delta=.01)
                self.assertLessEqual(fitted['slopes'][0],0.)
                self.assertAlmostEqual(curve_at(result,100),85.+slope*100/60,delta=.01)
                self.assertLessEqual(np.nanmax(result.x),BASE+minutes[-1]*60)

    def test_positive_drift_is_not_allowed_even_when_data_rises(self):
        minutes=np.arange(121,dtype=float)
        events=[event(-10.5,1),event(-10.5,position='Load')]
        result=fit_steps(BASE+minutes*60,85.+8.*minutes/60,events)
        fitted=next(iter(result.cache.values()))['fit']
        self.assertTrue(all(slope<=0. for slope in fitted['slopes']))
        self.assertAlmostEqual(fitted['slopes'][0],0.,delta=.01)

    def test_cache_from_signed_drift_model_is_not_reused(self):
        x,y,events=signal()
        current=fit_steps(x,y,events)
        old={}
        for key,value in current.cache.items():
            legacy=copy.deepcopy(value)
            legacy['fit']['slopes']=[100.]*len(legacy['fit']['slopes'])
            old[key[:-1]+('weibull-cauchy-linear-drift-v1',)]=legacy
        result=fit_steps(x,y,events,cache=old)
        self.assertGreater(result.fits_computed,0)
        self.assertTrue(all(slope<=0. for item in result.cache.values() for slope in item['fit']['slopes']))

    def test_drift_continues_through_b_changes_and_recovers_transition_time(self):
        minutes=np.arange(181,dtype=float)
        y=evaluate(minutes,np.array([20.]),np.array([150.,120.]),np.array([8.]),
            FitOptions(),np.array([0.,-12.]))
        y+=np.random.default_rng(41).normal(0,.2,len(y))
        y[[45,80,130]]=15.
        events=[event(-10.5,1),event(-10.5,position='Load'),event(9.5,2),
            event(40,position='Inject'),event(100,position='Load')]
        prefix=fit_steps(BASE+minutes[:90]*60,y[:90],events)
        result=fit_steps(BASE+minutes*60,y,events,cache=prefix.cache)
        fitted=next(iter(result.cache.values()))['fit']
        self.assertEqual(fitted['edges'],[BASE+9.5*60])
        self.assertEqual(len(fitted['slopes']),2)
        self.assertAlmostEqual(fitted['slopes'][1],-12.,delta=.5)
        self.assertAlmostEqual(fitted['durations'][0],8.,delta=.5)
        self.assertEqual(result.fits_computed,1)
        self.assertLessEqual(np.nanmax(prefix.x),BASE+minutes[89]*60)
        self.assertLessEqual(np.nanmax(result.x),BASE+minutes[-1]*60)

    def test_new_a_phase_starts_from_previous_value_including_drift(self):
        options=FitOptions()
        starts=np.array([20.,45.]);levels=np.array([150.,100.,130.])
        durations=np.array([6.,8.]);slopes=np.array([-6.,12.,-9.])
        self.assertAlmostEqual(evaluate(np.array([20.]),starts,levels,durations,options,slopes)[0],148.)
        for start in starts:
            values=evaluate(np.array([start-1e-7,start,start+1e-7]),starts,levels,durations,options,slopes)
            self.assertLess(np.ptp(values),1e-5)

    def test_analytic_drift_jacobian_matches_finite_differences(self):
        starts=np.array([10.,30.]);x=np.array([0.,5.,10.,12.,20.,30.,31.,40.,70.])
        p=np.array([150.,90.,120.,6.,8.,-2.,5.,-3.])
        def calculate(v):
            return _evaluate_model(x,starts,v[:3],v[3:5],v[5:],FitOptions(),with_jacobian=True)
        _,jac=calculate(p)
        numeric=np.empty_like(jac)
        for j in range(len(p)):
            plus,minus=p.copy(),p.copy();plus[j]+=1e-4;minus[j]-=1e-4
            numeric[:,j]=(calculate(plus)[0]-calculate(minus)[0])/2e-4
        np.testing.assert_allclose(jac,numeric,atol=1e-5,rtol=1e-4)

    def test_low_current_is_valid_when_it_is_the_actual_plateau(self):
        x, y, events = signal()
        result = fit_steps(x, y, events)
        self.assertAlmostEqual(curve_at(result, 10), 150., delta=2.)
        self.assertAlmostEqual(curve_at(result, 60), 15., delta=1.)
        shifted = fit_steps(x, y+100., events)
        np.testing.assert_allclose(shifted.y-100., result.y, atol=.01, equal_nan=True)

    def test_delay_is_applied_once_and_new_phase_waits_for_points(self):
        x, y, events = signal()
        early = fit_steps(x[:25], y[:25], events)
        self.assertTrue(np.all(early.x[np.isfinite(early.x)] <= BASE+20*60))
        enough = fit_steps(x[:31], y[:31], events)
        self.assertGreater(np.nanmax(enough.x), BASE+20*60)
        self.assertLess(curve_at(enough, 25), 50.)

    def test_identical_data_uses_cache(self):
        x, y, events = signal()
        first = fit_steps(x, y, events)
        second = fit_steps(x, y, events, cache=first.cache)
        self.assertEqual(second.fits_computed, 0)
        np.testing.assert_array_equal(first.y, second.y)

    def test_inject_and_load_return_continue_the_existing_a_phase(self):
        minutes = np.arange(81,dtype=float)
        x = BASE+minutes*60
        y = evaluate(minutes,np.array([20.]),np.array([150.,85.]),np.array([15.]),FitOptions())
        events = [event(-10.5,1),event(-10.5,position="Load"),
                  event(9.5,2),event(14.5,position="Inject"),event(29.5,position="Load")]
        result = fit_steps(x,y,events)
        reference = fit_steps(x,y,events[:3])
        np.testing.assert_array_equal(result.y,reference.y)
        fitted = next(iter(result.cache.values()))['fit']
        self.assertEqual(fitted['edges'],[BASE+9.5*60])
        self.assertEqual(len(fitted['durations']),1)
        self.assertEqual(len(valve_states(events,x[-1])),4)
        self.assertEqual(len(a_port_states(events,x[-1])),2)
        finite = result.x[np.isfinite(result.x)]
        self.assertTrue(np.any((finite>BASE+25*60)&(finite<BASE+40*60)))
        cached = fit_steps(x,y,events,cache=reference.cache)
        self.assertEqual(cached.fits_computed,0)

    def test_a_change_during_inject_still_starts_a_delayed_step(self):
        minutes=np.arange(81,dtype=float)
        x=BASE+minutes*60
        y=evaluate(minutes,np.array([20.,40.]),np.array([85.,120.,85.]),np.array([6.,6.]),FitOptions())
        events=[event(-10.5,1),event(-10.5,position='Load'),event(5,position='Inject'),
                event(9.5,2),event(29.5,1),event(49.5,position='Load')]
        result=fit_steps(x,y,events)
        fitted=next(iter(result.cache.values()))['fit']
        self.assertEqual(fitted['edges'],[BASE+9.5*60,BASE+29.5*60])
        self.assertTrue(all(slope<=0. for slope in fitted['slopes']))
        self.assertAlmostEqual(curve_at(result,35),120.,delta=1.)

    def test_b_change_does_not_reset_live_point_threshold(self):
        x,y,events=signal()
        events=events+[event(15,position='Inject')]
        result=fit_steps(x[:31],y[:31],events)
        self.assertGreater(np.nanmax(result.x),BASE+29*60)

    def test_short_finished_a_phase_is_not_a_permanent_hole(self):
        minutes = np.arange(71,dtype=float)
        x = BASE+minutes*60
        y = evaluate(minutes,np.array([25.,40.,45.]),np.array([85.,70.,85.,120.]),np.array([3.,4.,6.]),FitOptions())
        events = [event(-10.5,1),event(-10.5,position="Load"),
                  event(14.5,2),event(29.5,3),event(34.5,4)]
        live = fit_steps(x[:44],y[:44],events)
        self.assertFalse(np.any(live.x[np.isfinite(live.x)]>BASE+40*60))
        full = fit_steps(x,y,events)
        finite = full.x[np.isfinite(full.x)]
        self.assertTrue(np.any((finite>BASE+40*60)&(finite<BASE+45*60)))

    def test_real_data_gap_and_completed_block_survive_new_points(self):
        x = BASE+np.arange(57)*60
        y = np.full(len(x), 85.)
        keep = (x<BASE+25*60)|(x>=BASE+40*60)
        x,y=x[keep],y[keep]
        events = [event(-10.5,1),event(-10.5,position="Load")]
        first = fit_steps(x[:-1], y[:-1], events)
        second = fit_steps(x, y, events, cache=first.cache)
        self.assertEqual(second.fits_computed, 1)
        finite = second.x[np.isfinite(second.x)]
        self.assertFalse(np.any((finite >= BASE+25*60) & (finite < BASE+40*60)))
        old = first.x < BASE+25*60
        new = second.x < BASE+25*60
        np.testing.assert_array_equal(first.y[old], second.y[new])

    def test_no_actual_valve_log_means_no_fit(self):
        x, y, _ = signal()
        result = fit_steps(x, y, [])
        self.assertEqual(len(result.x), 0)
        self.assertIn("log", result.status)

    def test_future_events_do_not_change_existing_curve(self):
        x, y, events = signal()
        first = fit_steps(x[:40], y[:40], events)
        later = events+[event(50, 7), event(50, position="Inject")]
        second = fit_steps(x[:40], y[:40], later)
        np.testing.assert_array_equal(first.y, second.y)

    def test_fixed_downsampling_matches_expected_times(self):
        x, y = np.arange(12.), np.arange(12.)**2
        xx, yy = prepare_series(x, y, 3, "subsample", 7)
        np.testing.assert_array_equal(xx, x[1::3])
        np.testing.assert_array_equal(yy, y[1::3])
        xx, yy = prepare_series(x, y, 3, "mean")
        np.testing.assert_array_equal(xx, [1, 4, 7, 10])
        np.testing.assert_allclose(yy, y.reshape(4, 3).mean(axis=1))
        xx, yy = prepare_series(x, y, 3, "peak")
        np.testing.assert_array_equal(xx, [1, 1, 4, 4, 7, 7, 10, 10])
        np.testing.assert_array_equal(yy, [4, 0, 25, 9, 64, 36, 121, 81])

    def test_recipe_rows_do_not_create_fit_edges(self):
        text = "\n".join([
            "[2026-11-12 10:00:00] Valve A: At port 1",
            "[2026-11-12 10:00:01] Valve B: Load (1-6, 2-3, 4-5) position",
            "[2026-11-12 10:05:00] Step 2/8: A:Port 2, B:Load for 15.0 min",
            "[2026-11-12 10:05:01] Valve A: At port 1",
            "[2026-11-12 10:05:02] Valve B: Load position",
            "[2026-11-12 10:10:00] Valve A: At port 2",
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'session.log'
            path.write_text(text, encoding='utf-8')
            parsed = LogParser().parse(str(path))
        self.assertEqual(len(parsed.step_events), 1)
        states = valve_states(parsed.valve_events, datetime(2026, 11, 12, 11).timestamp())
        self.assertEqual([s.port for s in states], [1, 2])


if __name__ == '__main__':
    unittest.main()
