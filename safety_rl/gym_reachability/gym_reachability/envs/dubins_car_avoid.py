"""
Please contact the author(s) of this library if you have any questions.
Authors: Kai-Chieh Hsu        ( kaichieh@princeton.edu )

This module implements an environment considering Dubins car dynamics. This
environemnt shows reach-avoid reinforcement learning's performance on a
well-known reachability analysis benchmark.
"""

import gym.spaces
import numpy as np
import gym
import matplotlib.pyplot as plt
from matplotlib.ticker import LinearLocator
import torch
import random

from .dubins_car_dyn import DubinsCarDyn
from .env_utils import plot_arc, plot_circle


class DubinsCarAvoidEnv(gym.Env):
  """A gym environment considering Dubins car dynamics.
  """

  def __init__(
      self, device, config=None, mode="Avoid", doneType="toEnd", sample_inside_obs=False,
  ):
    """Initializes the environment with given arguments.

    Args:
        device (str): device type (used in PyTorch).
        mode (str, optional): reinforcement learning type. Defaults to "RA".
        doneType (str, optional): the condition to raise `done flag in
            training. Defaults to "toEnd".
        sample_inside_obs (bool, optional): consider sampling the state inside
            the obstacles if True. Defaults to False.

    """

    # Set random seed.
    self.set_seed(0)

    # State bounds.
    self.bounds = np.array([[-1.1, 1.1], [-1.1, 1.1], [0, 2 * np.pi]])
    self.low = self.bounds[:, 0]
    self.high = self.bounds[:, 1]
    self.sample_inside_obs = sample_inside_obs

    # Gym variables.
    self.action_space = gym.spaces.Discrete(3)
    midpoint = (self.low + self.high) / 2.0
    interval = self.high - self.low
    self.observation_space = gym.spaces.Box(
        np.float32(midpoint - interval/2),
        np.float32(midpoint + interval/2),
    )

    # Constraint set parameters.
    self.constraint_center = np.array([0, 0])
    self.constraint_radius = 0.5

    # Internal state.
    self.mode = mode
    self.state = np.zeros(3)
    self.doneType = doneType

    # Dubins car parameters.
    self.time_step = 0.05
    self.speed = 1.  # v
    self.R_turn = 0.8
    self.car = DubinsCarDyn(doneType=doneType)
    self.init_car()
    self.config = config

    # Visualization params
    self.visual_initial_states = [
        np.array([0.6 * self.constraint_radius, -0.5, np.pi / 2]),
        np.array([-0.4 * self.constraint_radius, -0.5, np.pi / 2]),
        np.array([-0.95 * self.constraint_radius, 0.0, np.pi / 2]),
        np.array([
            self.R_turn,
            0.95 * (self.constraint_radius - self.R_turn),
            np.pi / 2,
        ]),
    ]
    # Cost Params
    self.safetyScaling = 1.0
    self.penalty = 1.0
    self.costType = "sparse"
    self.device = device
    self.scaling = 1.0

    print(
        "Env: mode-{:s}; doneType-{:s}; sample_inside_obs-{}".format(
            self.mode, self.doneType, self.sample_inside_obs
        )
    )

  def init_car(self):
    """
    Initializes the dynamics and constraint set of a Dubins car.
    """
    self.car.set_bounds(bounds=self.bounds)
    self.car.set_constraint(
        center=self.constraint_center, radius=self.constraint_radius
    )
    self.car.set_speed(speed=self.speed)
    self.car.set_time_step(time_step=self.time_step)
    self.car.set_radius_rotation(R_turn=self.R_turn, verbose=False)

  # == Reset Functions ==
  def reset(self, start=None):
    """Resets the state of the environment.

    Args:
        start (np.ndarray, optional): state to reset the environment to.
            If None, pick the state uniformly at random. Defaults to None.

    Returns:
        np.ndarray: The state that the environment has been reset to.
    """
    self.state = self.car.reset(
        start=start,
        sample_inside_obs=self.sample_inside_obs,
    )
    return np.copy(self.state)

  def sample_random_state(
      self, sample_inside_obs=False,  theta=None
  ):
    """Picks the state of the environment uniformly at random.

    Args:
        sample_inside_obs (bool, optional): consider sampling the state inside
            the obstacles if True. Defaults to False.
        theta (float, optional): if provided, set the initial heading angle
            (yaw). Defaults to None.

    Returns:
        np.ndarray: the sampled initial state.
    """
    state = self.car.sample_random_state(
        sample_inside_obs=sample_inside_obs,
        theta=theta,
    )
    return state

  # == Dynamics Functions ==
  def step(self, action):
    """Evolves the environment one step forward given an action.

    Args:
        action (int): the index of the action in the action set.

    Returns:
        np.ndarray: next state.
        float: the standard cost used in reinforcement learning.
        bool: True if the episode is terminated.
        dict: consist of safety margin at the new state.
    """
    distance = np.linalg.norm(self.state - self.car.state)
    assert distance < 1e-8, (
        "There is a mismatch between the env state"
        + "and car state: {:.2e}".format(distance)
    )

    state_nxt, _ = self.car.step(action)
    self.state = state_nxt
    g_x = self.safety_margin(self.state[:2])

    fail = g_x < 0

    # cost
    if self.mode == "avoid" or self.mode == "RA":
      if fail:
        cost = self.penalty
      else:
        cost = 0.0
    else:
      if fail:
        cost = self.penalty
      else:
        if self.costType == "sparse":
          cost = 0.0 * self.scaling
        elif self.costType == "max_ell_g":
          cost = g_x
        else:
          cost = 0.0

    # = `done` signal
    if self.doneType == "toEnd":
      done = not self.car.check_within_bounds(self.state)
    elif self.doneType == "fail":
      done = fail
    elif self.doneType == "TF":
      done = fail
    else:
      raise ValueError("invalid done type!")

    # = `info`
    if done and self.doneType == "fail":
      info = {"g_x": self.penalty * self.scaling}
    else:
      info = {"g_x": g_x,}
    return np.copy(self.state), cost, done, info

  # == Setting Hyper-Parameter Functions ==
  def set_costParam(
      self, penalty=1.0, costType="sparse",
      safetyScaling=1.0
  ):
    """
    Sets the hyper-parameters for the `cost` signal used in training, important
    for standard (Lagrange-type) reinforcement learning.

    Args:
        penalty (float, optional): cost when entering the obstacles or
            crossing the environment boundary. Defaults to 1.0.
        costType (str, optional): providing extra information when in
            neither the failure set 
            Defaults to 'sparse'.

        safetyScaling (float, optional): scaling factor of the safety
            margin. Defaults to 1.0.
    """
    self.penalty = penalty
    self.costType = costType
    self.safetyScaling = safetyScaling

  def set_seed(self, seed):
    """Sets the seed for `numpy`, `random`, `PyTorch` packages.

    Args:
        seed (int): seed value.
    """
    self.seed_val = seed
    np.random.seed(self.seed_val)
    torch.manual_seed(self.seed_val)
    torch.cuda.manual_seed(self.seed_val)
    # if you are using multi-GPU.
    torch.cuda.manual_seed_all(self.seed_val)
    random.seed(self.seed_val)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True

  def set_bounds(self, bounds):
    """Sets the boundary and the observation space of the environment.

    Args:
        bounds (np.ndarray): of the shape (n_dim, 2). Each row is [LB, UB].
    """
    self.bounds = bounds

    # Get lower and upper bounds
    self.low = np.array(self.bounds)[:, 0]
    self.high = np.array(self.bounds)[:, 1]

    # Double the range in each state dimension for Gym interface.
    midpoint = (self.low + self.high) / 2.0
    interval = self.high - self.low
    self.observation_space = gym.spaces.Box(
        np.float32(midpoint - interval/2),
        np.float32(midpoint + interval/2),
    )
    self.car.set_bounds(bounds)

  def set_speed(self, speed=0.5):
    """Sets the linear velocity of the car.

    Args:
        speed (float, optional): speed of the car. Defaults to .5.
    """
    self.speed = speed
    self.car.set_speed(speed=speed)

  def set_radius(self, constraint_radius=1.0, R_turn=0.6):
    """Sets constraint_radius and turning radius.

    Args:
        constraint_radius (float, optional): the radius of the constraint set.
            Defaults to 1.0.
        R_turn (float, optional): the radius of the car's circular motion.
            Defaults to .6.
    """
    self.constraint_radius = constraint_radius
    self.R_turn = R_turn
    self.car.set_radius(
        constraint_radius=constraint_radius,
        R_turn=R_turn
    )

  def set_radius_rotation(self, R_turn=0.6, verbose=False):
    """
    Sets radius of the car's circular motion. The turning radius influences the
    angular speed and the discrete control set.

    Args:
        R_turn (float, optional): the radius of the car's circular motion.
            Defaults to .6.
        verbose (bool, optional): print messages if True. Defaults to False.
    """
    self.R_turn = R_turn
    self.car.set_radius_rotation(R_turn=R_turn, verbose=verbose)

  def set_constraint(self, center=np.array([0.0, 0.0]), radius=1.0):
    """Sets the constraint set (complement of failure set).

    Args:
        center (np.ndarray, optional): center of the constraint set.
            Defaults to np.array([0.,0.]).
        radius (float, optional): radius of the constraint set.
            Defaults to 1.0.
    """
    self.constraint_center = center
    self.constraint_radius = radius
    self.car.set_constraint(center=center, radius=radius)


  # == Margin Functions ==
  def safety_margin(self, s):
    """Computes the margin (e.g. distance) between the state and the failue set.

    Args:
        s (np.ndarray): the state of the agent.

    Returns:
        float: postivive numbers indicate being inside the failure set (safety
            violation).
    """
    return self.car.safety_margin(s[:2], avoid=True) # default is that positive <-> failure, flip for convention


  # == Getting Functions ==
  def get_warmup_examples(self, num_warmup_samples=100):
    """Gets warmup samples.

    Args:
        num_warmup_samples (int, optional): # warmup samples. Defaults to 100.

    Returns:
        np.ndarray: sampled states.
        np.ndarray: the heuristic values, here we used max{ell, g}.
    """
    rv = np.random.uniform(
        low=self.low, high=self.high, size=(num_warmup_samples, 3)
    )
    x_rnd, y_rnd, theta_rnd = rv[:, 0], rv[:, 1], rv[:, 2]

    heuristic_v = np.zeros((num_warmup_samples, self.action_space.n))
    states = np.zeros((num_warmup_samples, self.observation_space.shape[0]))

    for i in range(num_warmup_samples):
      x, y, theta = x_rnd[i], y_rnd[i], theta_rnd[i]
      g_x = self.safety_margin(np.array([x, y]))
      heuristic_v[i, :] = g_x
      states[i, :] = x, y, theta

    return states, heuristic_v

  def get_axes(self):
    """Gets the axes bounds and aspect_ratio.

    Returns:
        np.ndarray: axes bounds.
        float: aspect ratio.
    """
    aspect_ratio = ((self.bounds[0, 1] - self.bounds[0, 0]) /
                    (self.bounds[1, 1] - self.bounds[1, 0]))
    axes = np.array([
        self.bounds[0, 0],
        self.bounds[0, 1],
        self.bounds[1, 0],
        self.bounds[1, 1],
    ])
    return [axes, aspect_ratio]

  def get_value(self, q_func, theta, nx=101, ny=101, addBias=False):
    """
    Gets the state values given the Q-network. We fix the heading angle of the
    car to `theta`.

    Args:
        q_func (object): agent's Q-network.
        theta (float): the heading angle of the car.
        nx (int, optional): # points in x-axis. Defaults to 101.
        ny (int, optional): # points in y-axis. Defaults to 101.
        addBias (bool, optional): adding bias to the values or not.
            Defaults to False.

    Returns:
        np.ndarray: values
    """
    v = np.zeros((nx, ny))
    it = np.nditer(v, flags=["multi_index"])
    xs = np.linspace(self.bounds[0, 0], self.bounds[0, 1], nx)
    ys = np.linspace(self.bounds[1, 0], self.bounds[1, 1], ny)
    while not it.finished:
      idx = it.multi_index
      x = xs[idx[0]]
      y = ys[idx[1]]
      g_x = self.safety_margin(np.array([x, y]))

      if self.mode == "normal" or self.mode == "RA":
        state = (torch.FloatTensor([x, y, theta]).to(self.device).unsqueeze(0))
      else:
        z = g_x
        state = (
            torch.FloatTensor([x, y, theta, z]).to(self.device).unsqueeze(0)
        )
      if addBias:
        v[idx] = q_func(state).max(dim=1)[0].item() + g_x
      else:
        v[idx] = q_func(state).max(dim=1)[0].item()
      it.iternext()
    return v

  # == Trajectory Functions ==
  def simulate_one_trajectory(
      self, q_func, T=10, state=None, theta=None, sample_inside_obs=True,
      toEnd=False
  ):
    """Simulates the trajectory given the state or randomly initialized.

    Args:
        q_func (object): agent's Q-network.
        T (int, optional): the maximum length of the trajectory. Defaults
            to 250.
        state (np.ndarray, optional): if provided, set the initial state to
            its value. Defaults to None.
        theta (float, optional): if provided, set the theta to its value.
            Defaults to None.
        sample_inside_obs (bool, optional): sampling initial states inside
            of the obstacles or not. Defaults to True.
        toEnd (bool, optional): simulate the trajectory until the robot
            crosses the boundary or not. Defaults to False.

    Returns:
        np.ndarray: states of the trajectory, of the shape (length, 3).
        int: result.
        float: the minimum reach-avoid value of the trajectory.
        dictionary: extra information, (v_x, g_x, ell_x) along the traj.
    """
    # reset
    if state is None:
      state = self.car.sample_random_state(
          sample_inside_obs=sample_inside_obs,
          theta=theta,
      )
    traj = []
    result = 0  # not finished
    valueList = []
    gxList = []
    for t in range(T):
      traj.append(state)

      g_x = self.safety_margin(state)

      # = Rollout Record
      if t == 0:
        minG = g_x
        current = minG
        minV = current
      else:
        minG = min(minG, g_x)
        current = minG
        minV = min(current, minV) # TODO check this

      valueList.append(minV)
      gxList.append(g_x)

      if toEnd:
        done = not self.car.check_within_bounds(state)
        if done:
          result = 1
          break
      else:
        if g_x < 0:
          result = -1  # failed
          break

      q_func.eval()
      state_tensor = (torch.FloatTensor(state).to(self.device).unsqueeze(0))
      action_index = q_func(state_tensor).max(dim=1)[1].item()
      u = self.car.discrete_controls[action_index]

      state = self.car.integrate_forward(state, u)
    traj = np.array(traj)
    info = {"valueList": valueList, "gxList": gxList}
    return traj, result, minV, info

  def simulate_trajectories(
      self, q_func, T=10, num_rnd_traj=None, states=None, toEnd=False
  ):
    """
    Simulates the trajectories. If the states are not provided, we pick the
    initial states from the discretized state space.

    Args:
        q_func (object): agent's Q-network.
        T (int, optional): the maximum length of the trajectory.
            Defaults to 250.
        num_rnd_traj (int, optional): #trajectories. Defaults to None.
        states (list of np.ndarray, optional): if provided, set the initial
            states to its value. Defaults to None.
        toEnd (bool, optional): simulate the trajectory until the robot
            crosses the boundary or not. Defaults to False.

    Returns:
        list of np.ndarray: each element is a tuple consisting of x and y
            positions along the trajectory.
        np.ndarray: the binary reach-avoid outcomes.
        np.ndarray: the minimum reach-avoid values of the trajectories.
    """
    assert ((num_rnd_traj is None and states is not None)
            or (num_rnd_traj is not None and states is None)
            or (len(states) == num_rnd_traj))
    trajectories = []

    if states is None:
      nx = 41
      ny = nx
      xs = np.linspace(self.bounds[0, 0], self.bounds[0, 1], nx)
      ys = np.linspace(self.bounds[1, 0], self.bounds[1, 1], ny)
      results = np.empty((nx, ny), dtype=int)
      minVs = np.empty((nx, ny), dtype=float)

      it = np.nditer(results, flags=["multi_index"])
      print()
      while not it.finished:
        idx = it.multi_index
        print(idx, end="\r")
        x = xs[idx[0]]
        y = ys[idx[1]]
        state = np.array([x, y, 0.0])
        traj, result, minV, _ = self.simulate_one_trajectory(
            q_func, T=T, state=state, toEnd=toEnd
        )
        trajectories.append((traj))
        results[idx] = result
        minVs[idx] = minV
        it.iternext()
      results = results.reshape(-1)
      minVs = minVs.reshape(-1)

    else:
      results = np.empty(shape=(len(states),), dtype=int)
      minVs = np.empty(shape=(len(states),), dtype=float)
      for idx, state in enumerate(states):
        traj, result, minV, _ = self.simulate_one_trajectory(
            q_func, T=T, state=state, toEnd=toEnd
        )
        trajectories.append(traj)
        results[idx] = result
        minVs[idx] = minV

    return trajectories, results, minVs

  # == Plotting Functions ==
  def render(self):
    pass

  def visualize(
      self, q_func, vmin=-1, vmax=1, nx=101, ny=101, cmap="seismic",
      labels=None, boolPlot=False, addBias=False, theta=np.pi / 2,
      rndTraj=False, num_rnd_traj=10
  ):
    """
    Visulaizes the trained Q-network in terms of state values and trajectories
    rollout.

    Args:
        q_func (object): agent's Q-network.
        vmin (int, optional): vmin in colormap. Defaults to -1.
        vmax (int, optional): vmax in colormap. Defaults to 1.
        nx (int, optional): # points in x-axis. Defaults to 101.
        ny (int, optional): # points in y-axis. Defaults to 101.
        cmap (str, optional): color map. Defaults to 'seismic'.
        labels (list, optional): x- and y- labels. Defaults to None.
        boolPlot (bool, optional): plot the values in binary form.
            Defaults to False.
        addBias (bool, optional): adding bias to the values or not.
            Defaults to False.
        theta (float, optional): if provided, set the theta to its value.
            Defaults to np.pi/2.
        rndTraj (bool, optional): randomli choose trajectories if True.
            Defaults to False.
        num_rnd_traj (int, optional): #trajectories. Defaults to None.
    """
    thetaList = [0, np.pi / 2, np.pi, np.pi * 3 / 2]
    fig = plt.figure(figsize=(12, 4))
    ax1 = fig.add_subplot(141)
    ax2 = fig.add_subplot(142)
    ax3 = fig.add_subplot(143)
    ax4 = fig.add_subplot(144)
    axList = [ax1, ax2, ax3, ax4]
    path = self.config.grid_path 

    for i, (ax, theta) in enumerate(zip(axList, thetaList)):
      # for i, (ax, theta) in enumerate(zip(self.axes, thetaList)):
      ax.cla()
      if i == len(thetaList) - 1:
        cbarPlot = True
      else:
        cbarPlot = False

      # == Plot failure set ==
      self.plot_failure_set(ax)
      v_grid = self.plot_grid_values(ax, orientation=theta, path=path)


      # == Plot V ==
      self.plot_v_values(
          q_func,
          ax=ax,
          fig=fig,
          theta=theta,
          vmin=vmin,
          vmax=vmax,
          nx=nx,
          ny=ny,
          cmap=cmap,
          boolPlot=boolPlot,
          cbarPlot=cbarPlot,
          addBias=addBias,
      )
      # == Formatting ==
      self.plot_formatting(ax=ax, labels=labels)

      # == Plot Trajectories ==
      if rndTraj:
        self.plot_trajectories(
            q_func,
            T=200,
            num_rnd_traj=num_rnd_traj,
            theta=theta,
            toEnd=False,
            ax=ax,
            c="y",
            lw=2,
            orientation=0,
        )
      else:
        # `visual_initial_states` are specified for theta = pi/2. Thus,
        # we need to use "orientation = theta-pi/2"
        self.plot_trajectories(
            q_func,
            T=200,
            states=self.visual_initial_states,
            toEnd=False,
            ax=ax,
            c="y",
            lw=2,
            orientation=theta - np.pi / 2,
        )

      ax.set_xlabel(
          r"$\theta={:.0f}^\circ$".format(theta * 180 / np.pi),
          fontsize=28,
      )

    plt.tight_layout()

  def get_grid_value(self, grid):
    nx = np.shape(grid)[0]
    ny = np.shape(grid)[1]
    nz = np.shape(grid)[2]

    v = np.zeros((nx, ny, nz))
    it = np.nditer(v, flags=["multi_index"])
    while not it.finished:
      idx = it.multi_index
      v[idx] = grid[idx[0],idx[1], idx[2]]
      it.iternext()
    return v
  def plot_grid_values(self, ax, orientation, path, fig=None, vmin=-1, vmax=1, cmap="seismic"):
    orientation = np.array(orientation).copy()
    if orientation < 0:
        orientation += 2*np.pi
    grid = np.load(path)
    nx = np.shape(grid)[0]
    ny = np.shape(grid)[1]
    nz = np.shape(grid)[2]
    lin = np.linspace(0, 2*np.pi, num=nz)
    diff_lin = np.abs(lin-orientation)
    idx = np.argmin(diff_lin)
    diff = lin[idx] - orientation
    if diff > 0:
        idx2 = idx-1
        diff2 = orientation - lin[idx2]
        w2 = diff /(diff+diff2)
    else:
        idx2 = idx+1
        diff2 = lin[idx2] - orientation
        w2 = -diff / (-diff + diff2)
    w1 = 1-w2

    v_grid = self.get_grid_value(grid)
    v1 = v_grid[:,:,idx]
    v2 = v_grid[:,:,idx2]
    v = w1*v1 + w2*v2

    X = np.linspace(self.bounds[0,0], self.bounds[0,1], nx)
    Y = np.linspace(self.bounds[1,0], self.bounds[1,1], ny)
    X, Y = np.meshgrid(X, Y)
    ax.contour(X, Y, v.T, levels=[0], colors='white', linewidths=2, zorder=1)
    return v
  def plot_v_values(
      self, q_func, theta=np.pi / 2, ax=None, fig=None, vmin=-1, vmax=1,
      nx=201, ny=201, cmap="seismic", boolPlot=False, cbarPlot=True,
      addBias=False
  ):
    """Plots state values.

    Args:
        q_func (object): agent's Q-network.
        theta (float, optional): if provided, fix the car's heading angle
            to its value. Defaults to np.pi/2.
        ax (matplotlib.axes.Axes, optional): Defaults to None.
        fig (matplotlib.figure, optional): Defaults to None.
        vmin (int, optional): vmin in colormap. Defaults to -1.
        vmax (int, optional): vmax in colormap. Defaults to 1.
        nx (int, optional): # points in x-axis. Defaults to 201.
        ny (int, optional): # points in y-axis. Defaults to 201.
        cmap (str, optional): color map. Defaults to 'seismic'.
        boolPlot (bool, optional): plot the values in binary form.
            Defaults to False.
        cbarPlot (bool, optional): plot the color bar or not. Defaults to True.
        addBias (bool, optional): adding bias to the values or not.
            Defaults to False.
    """
    axStyle = self.get_axes()
    ax.plot([0.0, 0.0], [axStyle[0][2], axStyle[0][3]], c="k")
    ax.plot([axStyle[0][0], axStyle[0][1]], [0.0, 0.0], c="k")

    # == Plot V ==
    if theta is None:
      theta = 2.0 * np.random.uniform() * np.pi
    v = self.get_value(q_func, theta, nx, ny, addBias=addBias)

    if True:
      im = ax.imshow(
          v.T > 0.0,
          interpolation="none",
          extent=axStyle[0],
          origin="lower",
          cmap=cmap,
          zorder=-1,
      )
    else:
      im = ax.imshow(
          v.T,
          interpolation="none",
          extent=axStyle[0],
          origin="lower",
          cmap=cmap,
          vmin=vmin,
          vmax=vmax,
          zorder=-1,
      )
      if cbarPlot:
        cbar = fig.colorbar(
            im,
            ax=ax,
            pad=0.01,
            fraction=0.05,
            shrink=0.95,
            ticks=[vmin, 0, vmax],
        )
        cbar.ax.set_yticklabels(labels=[vmin, 0, vmax], fontsize=16)

  def plot_trajectories(
      self, q_func, T=100, num_rnd_traj=None, states=None, theta=None,
      toEnd=False, ax=None, c="y", lw=1.5, orientation=0, zorder=2
  ):
    """Plots trajectories given the agent's Q-network.

    Args:
        q_func (object): agent's Q-network.
        T (int, optional): the maximum length of the trajectory.
            Defaults to 100.
        num_rnd_traj (int, optional): #states. Defaults to None.
        states (list of np.ndarray, optional): if provided, set the initial
            states to its value. Defaults to None.
        theta (float, optional): if provided, set the car's heading angle
            to its value. Defaults to None.
        toEnd (bool, optional): simulate the trajectory until the robot
            crosses the boundary or not. Defaults to False.
        ax (matplotlib.axes.Axes, optional): Defaults to None.
        c (str, optional): color. Defaults to 'y'.
        lw (float, optional): linewidth. Defaults to 1.5.
        orientation (float, optional): counter-clockwise angle. Defaults
            to 0.
        zorder (int, optional): graph layers order. Defaults to 2.

    Returns:
        np.ndarray: the binary reach-avoid outcomes.
        np.ndarray: the minimum reach-avoid values of the trajectories.
    """
    assert ((num_rnd_traj is None and states is not None)
            or (num_rnd_traj is not None and states is None)
            or (len(states) == num_rnd_traj))

    if states is not None:
      tmpStates = []
      for state in states:
        x, y, theta = state
        xtilde = x * np.cos(orientation) - y * np.sin(orientation)
        ytilde = y * np.cos(orientation) + x * np.sin(orientation)
        thetatilde = theta + orientation
        tmpStates.append(np.array([xtilde, ytilde, thetatilde]))
      states = tmpStates

    trajectories, results, minVs = self.simulate_trajectories(
        q_func, T=T, num_rnd_traj=num_rnd_traj, states=states, toEnd=toEnd
    )
    if ax is None:
      ax = plt.gca()
    for traj in trajectories:
      traj_x = traj[:, 0]
      traj_y = traj[:, 1]
      ax.scatter(traj_x[0], traj_y[0], s=48, c=c, zorder=zorder)
      ax.plot(traj_x, traj_y, color=c, linewidth=lw, zorder=zorder)

    return results, minVs

  def plot_failure_set(self, ax=None, c_c="m", lw=3, zorder=0):
    """Plots the boundary of the failure set.

    Args:
        ax (matplotlib.axes.Axes, optional): ax to plot.
        c_c (str, optional): color of the constraint set boundary.
            Defaults to 'm'.
        lw (float, optional): linewidth of the boundary. Defaults to 3.
        zorder (int, optional): graph layers order. Defaults to 0.
    """
    plot_circle(
        self.constraint_center,
        self.constraint_radius,
        ax,
        c=c_c,
        lw=lw,
        zorder=zorder,
    )


  def plot_formatting(self, ax=None, labels=None):
    """Formats the visualization.

    Args:
        ax (matplotlib.axes.Axes, optional): ax to plot.
        labels (list, optional): x- and y- labels. Defaults to None.
    """
    axStyle = self.get_axes()
    # == Formatting ==
    ax.axis(axStyle[0])
    ax.set_aspect(axStyle[1])  # makes equal aspect ratio
    ax.grid(False)
    if labels is not None:
      ax.set_xlabel(labels[0], fontsize=52)
      ax.set_ylabel(labels[1], fontsize=52)

    ax.tick_params(
        axis="both",
        which="both",
        bottom=False,
        top=False,
        left=False,
        right=False,
    )
    ax.xaxis.set_major_locator(LinearLocator(5))
    ax.xaxis.set_major_formatter("{x:.1f}")
    ax.yaxis.set_major_locator(LinearLocator(5))
    ax.yaxis.set_major_formatter("{x:.1f}")
