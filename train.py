import numpy as np
import warnings
warnings.filterwarnings("ignore")
from torch.utils import data
from torch import nn, optim
from vis_tools import *
from datasets import *
from models import *
from Utilities import *
import argparse, os
import itertools
import torch
import time
import pdb
from torch.autograd import Variable
from torch.utils.tensorboard import SummaryWriter
print(torch.__version__)
print(torch.cuda.is_available())

# Create a summary writer
writer = SummaryWriter('runs/bicyclegan_experiment_1')

cuda = torch.cuda.is_available()
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
# Training Configurations 
# (You may put your needed configuration here. Please feel free to add more or use argparse. )
img_dir = '/home/zlz/BicycleGAN/datasets/edges2shoes/train/'
checkpoints_dir = '/Users/zy/Desktop/bicyclegan/checkpoints'
os.makedirs(checkpoints_dir, exist_ok=True)

img_shape = (3, 128, 128) # Please use this image dimension faster training purpose
num_epochs =  10
batch_size = 2
lr_rate = 0.0002 	      # Adam optimizer learning rate
betas = 0.5		  # Adam optimizer beta 1, beta 2
lambda_pixel = 10       # Loss weights for pixel loss
lambda_latent = 0.5      # Loss weights for latent regression 
lambda_kl = 0.01          # Loss weights for kl divergence
latent_dim = 8        # latent dimension for the encoded images from domain B
ndf = 64 # number of discriminator filters
# gpu_id = 
init_type='normal'
init_gain=0.02
netG='unet_128'
netD='basic_128'
norm='batch'
nl='relu'
use_dropout=False
where_add='input'
upsample='bilinear'
num_generator_filters = 64
output_nc=3	

# Normalize image tensor
def Normalize(image):
	return (image/255.0-0.5)*2.0

# Denormalize image tensor
def Denormalize(tensor):
	return ((tensor+1.0)/2.0)*255.0

# Reparameterization helper function 
# (You may need this helper function here or inside models.py, depending on your encoder implementation)


# Random seeds (optional)
torch.manual_seed(1); np.random.seed(1)

# Define DataLoader
dataset = Edge2Shoe(img_dir)
loader = data.DataLoader(dataset, batch_size=batch_size)
print('The number of training images = %d' % len(dataset))

# Loss functions
mae_loss = torch.nn.L1Loss().to(device)

# Define generator, encoder and discriminators
generator = Generator(latent_dim, img_shape,output_nc, num_generator_filters, netG, norm, nl,
             use_dropout, init_type, init_gain, where_add, upsample).to(device)
encoder = Encoder(latent_dim).to(device)
D_VAE = Discriminator(img_shape, ndf, netD, norm, nl, init_type, init_gain, num_Ds=1).to(device)
D_LR = Discriminator(img_shape, ndf, netD, norm, nl, init_type, init_gain, num_Ds=1).to(device)

# Define optimizers for networks
optimizer_E = torch.optim.Adam(encoder.parameters(), lr=lr_rate, betas=(betas,0.999))
optimizer_G = torch.optim.Adam(generator.parameters(), lr=lr_rate, betas=(betas,0.999))
optimizer_D_VAE = torch.optim.Adam(D_VAE.parameters(), lr=lr_rate, betas=(betas,0.999))
optimizer_D_LR = torch.optim.Adam(D_LR.parameters(), lr=lr_rate, betas=(betas,0.999))

# For adversarial loss
Tensor = torch.cuda.FloatTensor if cuda else torch.Tensor

# For adversarial loss (optional to use)
valid = 1; fake = 0

criterion_GAN = torch.nn.MSELoss().to(device)
criterion_pixel = torch.nn.L1Loss().to(device)
criterion_latent = torch.nn.L1Loss().to(device)
criterion_kl = torch.nn.KLDivLoss().to(device)

# Initialize a counter for the total number of iterations
global_step = 0
# Training
total_steps = len(loader)*num_epochs; step = 0
for e in range(num_epochs):
	start = time.time()
	for idx, data in enumerate(loader):
		loss_G = 0; loss_D_VAE = 0; loss_D_LR = 0
		# Log losses to TensorBoard
		writer.add_scalar('Loss/G', loss_G.item(), global_step=global_step)
		writer.add_scalar('Loss/D_VAE', loss_D_VAE.item(), global_step=global_step)
		writer.add_scalar('Loss/D_LR', loss_D_LR.item(), global_step=global_step)

        # Increment the global step counter
		global_step += 1

		########## Process Inputs ##########
		edge_tensor, rgb_tensor = data
		edge_tensor, rgb_tensor = norm(edge_tensor).to(device), norm(rgb_tensor).to(device)
		real_A = edge_tensor;real_B = rgb_tensor
		
		valid = Variable(Tensor(np.ones((real_A.size(0), *D_VAE.output_shape))), requires_grad=False)
		fake = Variable(Tensor(np.zeros((real_A.size(0), *D_VAE.output_shape))), requires_grad=False)

		b_size = real_B.size(0)
		noise = torch.randn(b_size, latent_dim, 1, 1, device=device)

		#-------------------------------
		#  Train Generator and Encoder
		#------------------------------
		encoder.train(); generator.train()
		optimizer_E.zero_grad(); optimizer_G.zero_grad()

		mean, log_var = encoder(real_B)
		z = reparameterization(mean, log_var)
		# KL loss
		kl_loss = criterion_kl(z,noise)

		#generator loss for VAE-GAN
		loss_VAE_GAN, fake_B_VAE = loss_generator(generator, real_A, z, D_VAE, valid, criterion_GAN)

		#generator loss for LR-GAN
		loss_LR_GAN, fake_B_LR = loss_generator(generator, real_A, z, D_LR, valid, criterion_GAN)


        #l1 loss between generated image and real image
		l1_image = loss_image(real_A, real_B, z, generator, criterion_pixel)

		#latent loss between encoded z and noise
		l1_latent = loss_latent(noise, real_A, encoder, generator, criterion_latent)

		loss_G = loss_VAE_GAN + loss_LR_GAN + lambda_pixel*l1_image + lambda_latent*l1_latent + lambda_kl*kl_loss

		loss_G.backward()
        # Update G
		optimizer_G.step()
		# Update E
		optimizer_E.step()
		#----------------------------------
		#  Train Discriminator (cVAE-GAN)
		#----------------------------------

		D_VAE.train()
		optimizer_D_VAE.zero_grad()
		#loss for D_VAE
		loss_D_VAE = loss_discriminator(D_VAE, real_B, generator, noise, valid, fake, criterion_GAN)
		loss_D_VAE.backward()
		optimizer_D_VAE.step()

		#---------------------------------
		#  Train Discriminator (cLR-GAN)
		#---------------------------------
		D_LR.train()
		optimizer_D_LR.zero_grad()
		#loss for D_LR
		loss_D_LR = loss_discriminator(D_LR, real_B, generator, noise, valid, fake, criterion_GAN)
		loss_D_LR.backward()
		optimizer_D_LR.step()

		""" Optional TODO: 
			1. You may want to visualize results during training for debugging purpose
			2. Save your model every few iterations
		"""
		if idx % 10 == 0:  # visualize every 10 batches
			visualize_images(denorm(fake_B_VAE.detach()).cpu(), 'Generated Images VAE')
			visualize_images(denorm(fake_B_LR.detach()).cpu(), 'Generated Images LR')
			visualize_images(denorm(real_B.detach()).cpu(), 'Real Images')

		if idx % 100 == 0:  # save every 100 batches
			torch.save(generator.state_dict(), os.path.join(checkpoints_dir, f'generator_epoch{e}_batch{idx}.pth'))
			torch.save(encoder.state_dict(), os.path.join(checkpoints_dir, f'encoder_epoch{e}_batch{idx}.pth'))
			torch.save(D_VAE.state_dict(), os.path.join(checkpoints_dir, f'D_VAE_epoch{e}_batch{idx}.pth'))
			torch.save(D_LR.state_dict(), os.path.join(checkpoints_dir, f'D_LR_epoch{e}_batch{idx}.pth'))