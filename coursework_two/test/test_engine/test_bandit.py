"""Contextual Thompson Sampling tests."""

import numpy as np
import pytest

from engine.bandit import BanditWeights, LinearThompsonSampler, build_arms


def test_build_arms_shape():
    """v0.3.2: 8 arms spanning momentum/value split (2-factor strategy)."""
    arms = build_arms()
    assert len(arms) == 8
    for a in arms:
        assert set(a.keys()) == {"momentum", "value", "quality", "sentiment"}
        assert abs(sum(a.values()) - 1.0) < 1e-6
        assert all(v >= 0 for v in a.values())


def test_build_arms_are_2factor_only():
    """All arms must concentrate on momentum + value; quality/sentiment = 0.

    The adopted v0.3.0 strategy has zero weight on quality and sentiment
    (FACTOR_REVIEW_2026-04-22.md).  A bandit arm that places positive
    weight on those factors would be proposing a strategy we've already
    empirically rejected.
    """
    arms = build_arms()
    for a in arms:
        assert a["quality"] == 0.0
        assert a["sentiment"] == 0.0
        assert a["momentum"] > 0
        assert a["value"] > 0


def test_build_arms_span_meaningful_range():
    """Arms should include genuinely momentum-heavy, value-heavy, and neutral splits."""
    arms = build_arms()
    mom_weights = sorted(a["momentum"] for a in arms)
    # Symmetric span: at least one arm ≤ 0.3 momentum, at least one ≥ 0.7.
    assert min(mom_weights) <= 0.3
    assert max(mom_weights) >= 0.7
    # Baseline 0.5 / 0.5 must be present (adopted strategy).
    assert any(abs(a["momentum"] - 0.5) < 1e-9 and abs(a["value"] - 0.5) < 1e-9 for a in arms)


def test_linear_ts_converges_to_best_arm():
    """TS should eventually pick the arm with highest reward."""
    ts = LinearThompsonSampler(n_arms=3, context_dim=2, sigma2_prior=1.0, seed=123)
    rng = np.random.default_rng(0)
    # Arm 0 always pays 1.0, others pay 0
    for _ in range(200):
        ctx = rng.standard_normal(2)
        a = ts.sample_action(ctx)
        reward = 1.0 if a == 0 else 0.0
        ts.update(a, ctx, reward)
    # Posterior mean for arm 0 should have higher context-weighted score
    ctx_test = np.array([1.0, 1.0])
    scores = []
    for a in range(3):
        mu, _ = ts.posterior(a)
        scores.append(ctx_test @ mu)
    # Arm 0 best
    assert np.argmax(scores) == 0


def test_bandit_weights_returns_valid_arm(base_config):
    bw = BanditWeights(base_config)
    weights, arm_idx, ctx = bw.select(
        vix_level_z=0.5,
        regime_flag="normal",
        dispersion={"momentum": 0.1, "value": 0.2, "quality": 0.0, "sentiment": 0.0},
        ic_3mo={"momentum": 0.02, "value": 0.03, "quality": 0.0, "sentiment": 0.0},
    )
    assert 0 <= arm_idx < base_config.bandit.n_arms
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert ctx.shape == (base_config.bandit.context_dim,)


def test_bandit_log_row_format(base_config):
    bw = BanditWeights(base_config)
    bw.select(0.0, "normal", {"momentum": 0.0, "value": 0.0, "quality": 0.0, "sentiment": 0.0},
              {"momentum": 0.0, "value": 0.0, "quality": 0.0, "sentiment": 0.0})
    bw.update_reward(0.01)
    from datetime import date
    row = bw.log_row(date(2024, 1, 31))
    expected = {"date", "arm_selected", "realised_reward",
                "arm_posterior_mean_json", "arm_posterior_std_json", "context_vector_json"}
    assert expected.issubset(row.keys())
