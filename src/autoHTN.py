import pyhop
import json
import re

# turns recipe names into something python won't complain about
def _safe_name(s):
	s = s.lower()
	s = re.sub(r'[^a-z0-9_]+', '_', s)
	s = re.sub(r'_+', '_', s).strip('_')
	return s

# if we already have enough of something, we're done
def check_enough(state, ID, item, num):
	if getattr(state, item)[ID] >= num:
		return []
	return False

# otherwise try making it, then check again
def produce_enough(state, ID, item, num):
	return [('produce', ID, item), ('have_enough', ID, item, num)]

pyhop.declare_methods('have_enough', check_enough, produce_enough)

# generic router to produce_<item>
def produce(state, ID, item):
	return [('produce_{}'.format(item), ID)]

pyhop.declare_methods('produce', produce)

# builds one HTN method from one crafting recipe
def make_method(method_name, rule, op_name, tools_set):
	def method(state, ID):
		subtasks = []

		# tools you need but don't spend
		for tool, num in rule.get('Requires', {}).items():
			subtasks.append(('have_enough', ID, tool, num))

		# items you spend
		for item, num in rule.get('Consumes', {}).items():
			subtasks.append(('have_enough', ID, item, num))

		# actually do the thing
		subtasks.append((op_name, ID))
		return subtasks

	method.__name__ = method_name
	method._recipe_time = rule.get('Time', 999999)
	method._requires_tools = bool(rule.get('Requires'))
	return method

# declare all recipe methods
def declare_methods(data):
	methods_by_item = {}
	tools_set = set(data.get('Tools', []))

	for recipe_name, rule in data['Recipes'].items():
		for produced_item in rule['Produces'].keys():
			task = 'produce_{}'.format(produced_item)
			method_name = 'm_{}'.format(_safe_name(recipe_name))
			op_name = 'op_{}'.format(_safe_name(recipe_name))
			m = make_method(method_name, rule, op_name, tools_set)
			methods_by_item.setdefault(task, []).append(m)

	for task, ms in methods_by_item.items():
		# faster recipes first
		ms.sort(key=lambda m: m._recipe_time)
		pyhop.declare_methods(task, *ms)

# builds one operator from one crafting recipe
def make_operator(rule, op_name):
	def operator(state, ID):
		time_cost = rule.get('Time', 0)
		if state.time[ID] < time_cost:
			return False

		# check tools
		for tool, num in rule.get('Requires', {}).items():
			if getattr(state, tool)[ID] < num:
				return False

		# check consumables
		for item, num in rule.get('Consumes', {}).items():
			if getattr(state, item)[ID] < num:
				return False

		# spend consumables
		for item, num in rule.get('Consumes', {}).items():
			getattr(state, item)[ID] -= num

		# spend time
		state.time[ID] -= time_cost

		# gain produced items
		for item, num in rule.get('Produces', {}).items():
			getattr(state, item)[ID] += num

		return state

	operator.__name__ = op_name
	operator._recipe_time = rule.get('Time', 999999)
	return operator

# declare all operators
def declare_operators(data):
	ops = []
	for recipe_name, rule in data['Recipes'].items():
		op_name = 'op_{}'.format(_safe_name(recipe_name))
		ops.append(make_operator(rule, op_name))

	pyhop.declare_operators(*ops)

# heuristic to stop infinite loops and dumb branches
def add_heuristic(data, ID):
	def heuristic(state, curr_task, tasks, plan, depth, calling_stack):
		# hard recursion cap
		if depth > 80:
			return True

		# don't let produce_x loop on itself
		if curr_task[0].startswith('produce_') and curr_task in calling_stack:
			return True

		# negative time should never happen
		if state.time[ID] < 0:
			return True

		return False

	pyhop.add_check(heuristic)

# runtime ordering for produce_* methods
def define_ordering(data, ID):
	tools_set = set(data.get('Tools', []))

	def reorder_methods(state, curr_task, tasks, plan, depth, calling_stack, methods):
		def score(m):
			try:
				subtasks = pyhop.get_subtasks(m, state, curr_task)
			except Exception:
				subtasks = []

			missing_tool_penalty = 0
			for t in subtasks:
				if len(t) == 4 and t[0] == 'have_enough':
					thing, num = t[2], t[3]
					if thing in tools_set and getattr(state, thing)[ID] < num:
						missing_tool_penalty += 100

			return (missing_tool_penalty, getattr(m, '_recipe_time', 999999))

		return sorted(methods, key=score)

	pyhop.define_ordering(reorder_methods)

# initialize state from JSON
def set_up_state(data, ID):
	state = pyhop.State('state')
	setattr(state, 'time', {ID: data['Problem']['Time']})

	for item in data['Items']:
		setattr(state, item, {ID: 0})

	for item in data['Tools']:
		setattr(state, item, {ID: 0})

	for item, num in data['Problem']['Initial'].items():
		setattr(state, item, {ID: num})

	return state

# convert goal dict into HTN tasks
def set_up_goals(data, ID):
	return [('have_enough', ID, item, num) for item, num in data['Problem']['Goal'].items()]

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
	add_heuristic(data, 'agent')
	define_ordering(data, 'agent')

	# keep verbose low or it crawls
	pyhop.pyhop(state, goals, verbose=1)
