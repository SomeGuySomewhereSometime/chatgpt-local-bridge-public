"""Pure accessibility projections and matching; snapshot ownership stays in the worker."""
def ui_text(value) -> str:
    """Remove object-replacement/zero-width noise common in browser accessibility trees."""
    return str(value or '').replace('\ufffc', '').replace('\u200b', '').strip()


def compact_score(element: dict) -> int | None:
    role = str(element.get('role') or '')
    states = set(element.get('states') or ())
    name = ui_text(element.get('name'))
    text = ui_text(element.get('text'))
    actions = [a for a in element.get('actions', ()) if a and a != 'clickAncestor']
    interactive = {
        'button', 'toggle button', 'check box', 'radio button', 'entry', 'combo box',
        'password text', 'spin button', 'link', 'menu', 'menu item', 'check menu item',
        'radio menu item', 'page tab', 'page tab list', 'slider', 'scroll bar', 'list item',
    }
    attention = {'alert', 'dialog', 'notification', 'tool tip', 'menu'}
    context = {'heading', 'label', 'static'}
    wrappers = {'section', 'panel', 'landmark', 'scroll pane', 'internal frame', 'separator'}
    bounds = element.get('bounds')
    tiny = (isinstance(bounds, list) and len(bounds) == 4
            and isinstance(bounds[2], (int, float)) and isinstance(bounds[3], (int, float))
            and (bounds[2] <= 3 or bounds[3] <= 3))
    if tiny and not ({'focused', 'selected', 'expanded', 'checked', 'editable'} & states) and role not in attention:
        return None
    strong = (role in interactive or role in attention or role in context or actions
              or 'editable' in states or 'focused' in states or 'selected' in states
              or 'expanded' in states or 'checked' in states or 'value' in element)
    if role in wrappers and not strong and not name and not text:
        return None
    if not strong and not name and not text:
        return None
    score = 0
    if role in attention:
        score += 220
    if 'focused' in states:
        score += 180
    if 'selected' in states:
        score += 120
    if 'expanded' in states or 'checked' in states:
        score += 80
    if 'editable' in states:
        score += 140
    if role in interactive:
        score += 120
    if role == 'heading':
        score += 90
    elif role in context:
        score += 35
    if actions:
        score += 70
    if 'value' in element:
        score += 60
    if name:
        score += 45
    if text:
        score += 20
    if role in wrappers:
        score -= 70
    return score


LOCATOR_FIELDS = {'role', 'name', 'name_contains', 'text', 'text_contains', 'application',
                  'window', 'document', 'ancestor_role', 'ancestor_name', 'ancestor_name_contains'}


def validate_locator(locator, *, empty=False):
    if not isinstance(locator, dict) or set(locator) - LOCATOR_FIELDS:
        raise ValueError('Unsupported locator fields')
    if not empty and not locator:
        raise ValueError('locator must identify a target')
    for key, value in locator.items():
        if not isinstance(value, str) or not ui_text(value) or len(value) > 500:
            raise ValueError(f'locator.{key} must be nonempty text (at most 500 characters)')
    return locator


def matches(node, locator, nodes):
    norm = lambda x: ui_text(x).casefold()
    for key in ('role', 'name', 'text', 'application'):
        if key in locator and norm(node.get(key)) != norm(locator[key]):
            return False
    for key in ('name', 'text'):
        if key + '_contains' in locator and norm(locator[key + '_contains']) not in norm(node.get(key)):
            return False
    ancestors, seen = [], set()
    parent = node.get('parent')
    while parent in nodes and parent not in seen:
        seen.add(parent)
        ancestors.append(nodes[parent])
        parent = nodes[parent].get('parent')
    if 'window' in locator and not any(a.get('id', '').startswith('w') and norm(a.get('name')) == norm(locator['window']) for a in [node, *ancestors]):
        return False
    if 'document' in locator and not any(a.get('role') == 'document web' and norm(a.get('name')) == norm(locator['document']) for a in [node, *ancestors]):
        return False
    keys = ('ancestor_role', 'ancestor_name', 'ancestor_name_contains')
    if any(k in locator for k in keys):
        def ancestor_ok(a):
            return (('ancestor_role' not in locator or norm(a.get('role')) == norm(locator['ancestor_role']))
                    and ('ancestor_name' not in locator or norm(a.get('name')) == norm(locator['ancestor_name']))
                    and ('ancestor_name_contains' not in locator or norm(locator['ancestor_name_contains']) in norm(a.get('name'))))
        if not any(ancestor_ok(a) for a in ancestors):
            return False
    return True


def supports(node, kind):
    interfaces = node.get('interfaces', [])
    if kind == 'activate':
        return 'Action' in interfaces and bool(node.get('actions'))
    if kind == 'set_text':
        return 'EditableText' in interfaces
    if kind == 'select':
        return 'Selection' in interfaces
    if kind in ('focus', 'scroll_into_view'):
        return 'Component' in interfaces
    return True


def project(observation, nodes, mode, limit, offset=0, locator=None):
    """Project without mutating authoritative metadata; preserve raw ancestry internally."""
    elements = list(observation['elements'])
    if locator is not None:
        elements = [e for e in elements if matches(e, locator, nodes)]
    if mode == 'compact':
        ranked = [(compact_score(e), i, e) for i, e in enumerate(elements)
                  if 'showing' in e.get('states', [])]
        elements = [e for score, i, e in sorted((v for v in ranked if v[0] is not None), key=lambda v: (-v[0], v[1]))]
    chosen = elements[offset:offset + limit]
    kept = {e['id'] for e in chosen}
    output = []
    for e in chosen:
        item = dict(e)
        parent, seen = item.get('parent'), set()
        while parent in nodes and parent not in kept and not parent.startswith('w') and parent not in seen:
            seen.add(parent)
            parent = nodes[parent].get('parent')
        item['parent'] = parent
        output.append(item)
    result = dict(observation, elements=output, mode=mode)
    result['compact'] = {'scanned': len(observation['elements']), 'candidates': len(elements),
                         'returned': len(output), 'omitted': len(elements) - len(output)}
    result['next_offset'] = offset + len(output) if offset + len(output) < len(elements) else None
    result['attention'] = [{'id': e['id'], 'role': e['role'], 'name': e.get('name', '')} for e in chosen
                           if e.get('role') in ('alert', 'dialog', 'notification', 'menu')]
    # Truncation of the scan and omission from presentation are distinct.
    result['truncated'] = not observation.get('scan', {}).get('complete', True)
    return result
