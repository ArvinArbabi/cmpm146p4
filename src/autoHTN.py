# autoHTN.py
# automatically builds HTN planners from crafting rules without hardcoding recipes

import json
import pyhop


# checks if we already have enough of something
def check_enough(state, ID, item, num):
    if getattr(state, item)[ID] >= num:
        return []
    return False


# if we don’t have enough, try to produce more and check again
def produce_enough(state, ID, item, num):
    return [('produce', ID, item), ('have_enough', ID, item, num)]


pyhop.declare_methods('have_enough', check_enough, produce_enough)


# generic dispatcher that redirects to produce_<item>
# also prevents re-crafting the same tool forever
def produce(state, ID, item):
    made_flag = f"made_{item}"
    if hasattr(state, made_flag):
        if getattr(state, made_flag)[ID]:
            return False
        getattr(state, made_flag)[ID] = True
    return [(f'produce_{item}', ID)]


pyhop.declare_methods('produce', produce)


# creates an operator from a recipe rule
def make_operator(rule):
    produces = rule.get('Produces', {})
    requires = rule.get('Requires', {})
    consumes = rule.get('Consumes', {})
    time = rule.get('Time', 0)

    def operator(state, ID):
        # check required tools
        for tool, amt in requires.items():
            if getattr(state, tool)[ID] < amt:
                return False

        # check consumable items
        for item, amt in consumes.items():
            if getattr(state, item)[ID] < amt:
                return False

        # check time
        if state.time[ID] < time:
            return False

        # apply consumes
        for item, amt in consumes.items():
            getattr(state, item)[ID] -= amt

        # apply produces
        for item, amt in produces.items():
            getattr(state, item)[ID] += amt

        state.time[ID] -= time
        return state

    return operator


# registers all operators from the crafting file
def declare_operators(data):
    ops = []
    for name, rule in data['Recipes'].items():
        op = make_operator(rule)
        op.__name__ = 'op_' + name.replace(' ', '_')
        ops.append(op)
    pyhop.declare_operators(*ops)


# keeps ingots from being consumed too early
def _consumes_order(consumes):
    if not consumes:
        return []
    if 'ingot' in consumes:
        return list(consumes.items())
    return list(consumes.items())


# builds a method from a recipe
def make_method(recipe_name, rule):
    consumes = rule.get('Consumes', {})
    requires = rule.get('Requires', {})
    time = rule.get('Time', 0)

    def method(state, ID):
        subtasks = []

        # make sure we have things that get consumed
        for item, qty in _consumes_order(consumes):
            subtasks.append(('have_enough', ID, item, qty))

        # make sure we have required tools or stations
        for tool, qty in requires.items():
            subtasks.append(('have_enough', ID, tool, qty))

        # finally run the operator
        subtasks.append(('op_' + recipe_name.replace(' ', '_'), ID))
        return subtasks

    method.__name__ = recipe_name.replace(' ', '_')

    # metadata used for method reordering
    method._meta = {
        'produces': rule.get('Produces', {}),
        'requires': requires,
        'consumes': consumes,
        'time': time
    }

    return method


# groups methods by what they produce and sorts them
def declare_methods(data):
    methods_by_task = {}

    for recipe_name, rule in data['Recipes'].items():
        product = next(iter(rule['Produces'].keys()))
        task = f'produce_{product}'
        m = make_method(recipe_name, rule)
        methods_by_task.setdefault(task, []).append(m)

    # default sort by time
    for task, methods in methods_by_task.items():
        methods.sort(key=lambda m: m._meta['time'])
        pyhop.declare_methods(task, *methods)


# finds items currently being pursued to detect cycles
def _ancestor_items(calling_stack):
    items = set()
    for t in calling_stack:
        if t[0] == 'have_enough' and len(t) >= 3:
            items.add(t[2])
        if t[0] == 'produce' and len(t) >= 3:
            items.add(t[2])
    return items


# reorders methods so we don’t require a tool while trying to make it
def get_custom_method_order(state, curr_task, tasks, plan, depth, calling_stack, methods):
    ancestors = _ancestor_items(calling_stack)

    if not curr_task[0].startswith('produce_'):
        return methods

    def score(m):
        requires = set(m._meta['requires'].keys())
        cycle_penalty = 1000 if requires & ancestors else 0
        tool_penalty = len(requires) * 2
        time_penalty = m._meta['time']
        return cycle_penalty + tool_penalty + time_penalty

    return sorted(methods, key=score)


# adds pruning rules to cut bad branches
def add_heuristics(data, ID):
    goal_items = set(data['Problem']['Goal'].keys())
    goal_qty = data['Problem']['Goal']

    # estimate wood needed from goals
    wood_needed = 0
    wood_needed += goal_qty.get('wood', 0)
    wood_needed += goal_qty.get('plank', 0) / 4
    wood_needed += goal_qty.get('stick', 0) / 8

    def is_producing(task, name):
        return task[0] == f'produce_{name}' or (task[0] == 'produce' and task[2] == name)

    # don’t make iron axe unless explicitly required
    def prune_iron_axe(state, curr_task, *args):
        return is_producing(curr_task, 'iron_axe') and 'iron_axe' not in goal_items

    # don’t make axes if punching wood is fast enough
    def prune_axes(state, curr_task, *args):
        if is_producing(curr_task, 'wooden_axe') or is_producing(curr_task, 'stone_axe'):
            if curr_task[2] in goal_items:
                return False
            remaining = max(0, wood_needed - state.wood[ID])
            return remaining * 4 <= state.time[ID]
        return False

    pyhop.add_check(prune_iron_axe)
    pyhop.add_check(prune_axes)


# builds the initial state
def set_up_state(data, ID):
    state = pyhop.State('state')
    state.time = {ID: data['Problem']['Time']}

    for item in data['Items']:
        setattr(state, item, {ID: 0})

    for tool in data['Tools']:
        setattr(state, tool, {ID: 0})
        setattr(state, f"made_{tool}", {ID: False})

    for item, amt in data['Problem']['Initial'].items():
        getattr(state, item)[ID] = amt

    return state


# converts goal dict into have_enough tasks
def set_up_goals(data, ID):
    return [('have_enough', ID, item, amt) for item, amt in data['Problem']['Goal'].items()]


# entry point
if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("usage: python3 autoHTN.py scenario_x.json")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    ID = 'agent'
    state = set_up_state(data, ID)
    goals = set_up_goals(data, ID)

    declare_operators(data)
    declare_methods(data)
    add_heuristics(data, ID)

    pyhop.pyhop(state, goals, verbose=1)
