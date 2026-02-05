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


# adds pruning rules to cut bad branches
def add_heuristics(data, ID):
    def heuristic(state, curr_task, tasks, plan, depth, calling_stack):
        # get the goal
        goal = set(data["Problem"]["Goal"].keys())
        item = None
        
        # if the current task produces an item, get that item
        if curr_task[0].startswith("produce_"):
            item = curr_task[len("produce_"):]
        elif curr_task[0] == "produce":
            item = curr_task[2]

        # Never make any axes as too much tool production is what leads to the most infinite cycles
        if item is not None:
            if item == "iron_axe" and "iron_axe" not in goal:
                return True
                
            if item == "stone_axe" and "stone_axe" not in goal:
                return True
                
            if item == "wooden_axe" and "wooden_axe" not in goal:
                return True
            
        return False
        
    pyhop.add_check(heuristic)
        
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
    rules_filename = 'crafting.json'
    if len(sys.argv) > 1:
        rules_filename = sys.argv[1]

    with open(rules_filename) as f:
        data = json.load(f)

    state = set_up_state(data, 'agent')
    goals = set_up_goals(data, 'agent')

    declare_operators(data)
    declare_methods(data)
    add_heuristics(data, 'agent')

    pyhop.pyhop(state, goals, verbose=1)
