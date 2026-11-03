import pytest

from dtemp import regs


@pytest.mark.parametrize(
    "word, expected",
    [
        (0x0000, 0.0),
        (0x0004, 0.25),          # one LSB of the 11-bit value = 0.25 C
        (0x0190, 25.0),          # 100 * 0.25
        (0x0270, 39.0),          # a 39.00 C reading seen on real hardware via hwmon
        (0x0294, 41.25),
        (0x0FFC, 255.75),        # most positive value
        (0x1000, -256.0),        # most negative value
        (0x1FFC, -0.25),         # all value bits set = -1 LSB
        (0x1FF0, -1.0),
        (0xE190, 25.0),          # reserved bits 15:13 ignored
        (0x0193, 25.0),          # reserved bits 1:0 ignored
    ],
)
def test_spd5118_decode_vectors(word, expected):
    assert regs.decode_spd5118_word(word) == expected


def test_spd5118_resolution_is_quarter_degree_not_sixteenth():
    # The pre-release tool used 0.0625 C/LSB on unshifted bits; guard against a regression.
    assert regs.decode_spd5118_word(0x0004) - regs.decode_spd5118_word(0x0000) == 0.25


def test_spd5118_roundtrip_all_values():
    for value in range(-1024, 1024):
        temp = value * 0.25
        assert regs.decode_spd5118_word(regs.encode_spd5118_word(temp)) == temp


def test_spd5118_encode_clamps_to_range():
    assert regs.decode_spd5118_word(regs.encode_spd5118_word(999)) == regs.SPD5118_MAX_C
    assert regs.decode_spd5118_word(regs.encode_spd5118_word(-999)) == regs.SPD5118_MIN_C


@pytest.mark.parametrize("bad", [-1, 0x10000])
def test_decoders_reject_out_of_range_words(bad):
    with pytest.raises(ValueError):
        regs.decode_spd5118_word(bad)
    with pytest.raises(ValueError):
        regs.decode_jc42_word(bad)


@pytest.mark.parametrize(
    "reg, expected",
    [
        (0x0000, 0.0),
        (0x0190, 25.0),          # 0x190 = 400 * 0.0625
        (0x0001, 0.0625),
        (0x1FFF, -0.0625),
        (0x1000, -256.0),
        (0x0FFF, 255.9375),
        (0xE190, 25.0),          # flag bits 15:13 ignored
    ],
)
def test_jc42_decode_vectors(reg, expected):
    assert regs.decode_jc42_word(reg) == expected


def test_jc42_roundtrip():
    for value in range(-4096, 4096, 7):
        temp = value * 0.0625
        assert regs.decode_jc42_word(regs.encode_jc42_word(temp)) == temp


def test_swap16():
    assert regs.swap16(0x1234) == 0x3412


def test_plausible_range():
    assert regs.plausible(40.0)
    assert not regs.plausible(200.0)
    assert not regs.plausible(-100.0)
