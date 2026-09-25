from app.utils.numerals import extract_numbers, format_inr


def test_indian_grouping_and_currency():
    n = extract_numbers("Air ambulance up to INR 2,50,000 per hospitalisation")
    assert 250000.0 in n.money


def test_lakh_and_crore_units():
    n = extract_numbers("Maternity up to INR 1 Lac; sum insured 6 crores; 5 lakhs")
    assert 100000.0 in n.money and 60000000.0 in n.money and 500000.0 in n.money


def test_percent_and_duration():
    n = extract_numbers("co-payment of 20% ; 36 months waiting period ; 30 days")
    assert 20.0 in n.percents
    assert (36.0, "month") in n.durations and (30.0, "day") in n.durations


def test_percent_not_money():
    n = extract_numbers("50% of SI per year")
    assert n.money == []


def test_multiplier_and_unlimited():
    n = extract_numbers("Booster+ 5X unutilised sum insured; Automatic Restore unlimited times")
    assert 5.0 in n.multipliers and n.unlimited


def test_rupee_glyph_backtick_replaced_upstream():
    n = extract_numbers("Up to INR 10,000")
    assert 10000.0 in n.money


def test_format_inr():
    assert format_inr(500000) == "INR 5 lakh"
    assert format_inr(25000000) == "INR 2.5 crore"


def test_up_to_sum_insured_tolerates_determiners():
    # A pitch says "up to the base sum insured"; the brochure says "up to your Base Sum Insured".
    # Both must parse to the same marker or the numerical auditor fails a faithful claim.
    for phrase in ["up to the base sum insured", "up to your Base Sum Insured", "upto Sum Insured", "up to the SI", "100% of the sum insured"]:
        assert extract_numbers(phrase).up_to_sum_insured, phrase
    assert not extract_numbers("up to 60 days").up_to_sum_insured
