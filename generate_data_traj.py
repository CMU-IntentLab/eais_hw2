import matplotlib.pyplot as plt
import matplotlib.patches as patches
import argparse
import io
from PIL import Image
import numpy as np
import torch
import pickle
import pathlib



def gen_one_traj_img(x_min, x_max, y_min, y_max, u_max, dt, v, dpi, rand=-1):
  x_max -= 0.1
  y_max -= 0.1
  x_min += 0.1
  y_min += 0.1
  radius = 0.25
  center = (0.0, 0.0)
  states = torch.zeros(3)
  while torch.abs(states[0]) < radius and torch.abs(states[1]) < radius:
    states = torch.rand(3)
    states[0] *= x_max - x_min
    states[1] *= y_max - y_min
    states[0] += x_min
    states[1] += y_min 

  states[2] = torch.atan2(-states[1], -states[0]) + np.random.normal(0, 1)
  if states[2] < 0: 
    states[2] += 2*np.pi
  if states[2] > 2*np.pi: 
    states[2] -= 2*np.pi
  state_obs = []
  img_obs = []
  state_gt = []
  dones = []
  acs = []
  mapping = torch.tensor([-u_max, 0, u_max])

  for t in range(100):
    if torch.abs(states[0]) > 1.0 or torch.abs(states[1]) > 1.0:
      dones[-1] = 1
      break

    if rand == -1:
      random_integers = torch.randint(0, 3, (1,))
    else:
      random_integers = torch.tensor([rand])
    # Map 0 to -1, 1 to 0, and 2 to 1
    ac = mapping[random_integers].item()
    
    states_next = torch.rand(3)

    states_next[0] = states[0] + v*dt*torch.cos(states[2])
    states_next[1] = states[1] + v*dt*torch.sin(states[2])
    states_next[2] = states[2] + dt*ac

    state_obs.append(states[2].numpy()) # get to observe theta
    state_gt.append(states.numpy()) # gt state
    if t == 99:
      dones.append(1)
    else:
      dones.append(1)
    acs.append(ac)


    fig,ax = plt.subplots()
    plt.xlim([-1.1, 1.1])
    plt.ylim([-1.1, 1.1])
    plt.axis('off')
    fig.set_size_inches( 1, 1 )
    # Create the circle patch
    circle = patches.Circle(center, radius, edgecolor=(1,0,0), facecolor='none')
    # Add the circle patch to the axis
    ax.add_patch(circle)
    plt.quiver(states[0], states[1], dt*v*torch.cos(states[2]), dt*v*torch.sin(states[2]), angles='xy', scale_units='xy', minlength=0,width=0.1, scale=0.18,color=(0,0,1), zorder=3)
    plt.scatter(states[0], states[1],s=20, c=(0,0,1), zorder=3)
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=dpi)
    buf.seek(0)

    # Load the buffer content as an RGB image
    img = Image.open(buf).convert('RGB')
    img_array = np.array(img)
    img_obs.append(img_array)
    plt.close()
    states = states_next
  
  return state_obs, acs, state_gt, img_obs, dones

def generate_trajs(x_min, x_max, y_min, y_max, u_max, dt, v, num_pts, dpi):
  demos = []
  for i in range(num_pts):
    print('demo: ', i)
    state_obs, acs, state_gt, img_obs, dones = gen_one_traj_img(x_min, x_max, y_min, y_max, u_max, dt, v, dpi)
    demo = {}
    demo['obs'] = {'image': img_obs, 'state': state_obs, 'priv_state': state_gt}
    demo['actions'] = acs
    demo['dones'] = dones
    demos.append(demo)
  
  with open('wm_demos'+str(dpi)+'.pkl', 'wb') as f:
    pickle.dump(demos, f)

def recursive_update(base, update):
    for key, value in update.items():
        if isinstance(value, dict) and key in base:
            recursive_update(base[key], value)
        else:
            base[key] = value

if __name__=='__main__':      
    parser = argparse.ArgumentParser()
   




    
    num_pts =  2000 #config['num_pts']
    x_min = -1.1
    x_max = 1.1
    y_min = -1.1
    y_max = 1.1

    u_max = 1.1
    dt = 0.05
    v = 0.6

    dpi = 128
    demos = generate_trajs(x_min, x_max, y_min, y_max, u_max, dt, v, num_pts, dpi)
