from app.api.schemas import GroupType


class GroupTypeClassifier:
  def classify(self, context: str) -> GroupType:
    lowered = context.lower()

    if self._contains_any(lowered, ('答疑', 'q&a', 'support group', 'support')):
      return GroupType.QA

    if self._contains_any(lowered, ('售后', '客服', 'help desk', 'help center')):
      return GroupType.AFTER_SALES

    if self._contains_any(lowered, ('招募', '内测', 'beta', 'waitlist')):
      return GroupType.BETA

    if self._contains_any(
      lowered,
      (
        '交流群',
        '官方群',
        '飞书群',
        '微信群',
        'qq群',
        '社群',
        '社区',
        'community',
        'group',
        'developer group',
      ),
    ):
      return GroupType.DISCUSSION

    return GroupType.UNKNOWN

  def _contains_any(self, text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
