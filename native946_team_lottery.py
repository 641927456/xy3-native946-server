"""Preserve the original event closure for the festival treasure-hunting team."""

from native946_codec import text_notice

# The festival/2023 protocol survived the original event. The later
# configs.wdf/524ad5a5 closes its 2024 event on February 16.
PARAMETERS = {3836: 1, 3837: 1, 3838: 0, 3839: 1, 3840: 2,
              3854: 1, 3855: 1, 3861: 1, 4068: 1, 4069: 0, 4070: 2, 4071: 0}
REQUESTS = frozenset(PARAMETERS)


def receive(mid, reader):
    for _ in range(PARAMETERS[mid]):
        reader.integer()
    reader.finish()
    return (text_notice('寻宝小队抽奖活动已结束，当前没有开放场次。'),)
